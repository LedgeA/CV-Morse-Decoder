import time
from config import *
from morse_utils import *
from ui_utils import SessionChannel, SessionFrame, run_ui

class MorseDecoderApp:

    def __init__(self, ui_handler: SessionChannel):
        from audio_utils import AudioManager
        from vision_utils import VisionProcessor

        self.audio_manager = AudioManager()        # Handles sounds and speech
        self.vision_processor = VisionProcessor()  # Handles camera and eye detection
        self.ui_handler = ui_handler

        self.morse_sequence = ""
        self.decoded_text = ""
        self.invalid_sequence_detected = False  # Flag for invalid Morse codes
        
        # Control the sequential input system (DOT -> DASH -> SPACE cycling)
        self.sequence_index = 0
        self.sequence_start_time = 0.0  # When the current symbol started
        self.current_symbol_data = INPUT_SEQUENCE[0]  # Current symbol (. or - or space)
        self.is_interval_phase = False
        
        self.frame_count = 0
        self.calibration_done = False
        
        self.last_key_time = 0
        
        self.cap = None
    
    def initialize_camera(self):
        """Set up the camera with the right settings"""
        import cv2

        self.cap = cv2.VideoCapture(camera_index)
        
        self.cap.set(cv2.CAP_PROP_FRAME_WIDTH, CAMERA_WIDTH)
        self.cap.set(cv2.CAP_PROP_FRAME_HEIGHT, CAMERA_HEIGHT)
        self.cap.set(cv2.CAP_PROP_FPS, TARGET_FPS)
        
        if not self.cap.isOpened():
            raise RuntimeError("Could not open the camera. Check that it is connected and available.")
        
        actual_fps = self.cap.get(cv2.CAP_PROP_FPS)
        print(f"Attempted to set FPS to {TARGET_FPS}. Actual FPS: {actual_fps}")
    
    def handle_key_input(self, key: int, current_time: float):
        # Ignore keys if pressed too quickly (prevent double-presses)
        if current_time - self.last_key_time <= KEY_COOLDOWN:
            return True
        
        if key == ord('q') or key == ord('Q'):
            return False  # Quit the application
        
        elif key in [ord('r'), ord('R')]:
            self._handle_reset(current_time)  # Clear current Morse sequence
        
        elif key == 13:  # Enter key
            self._handle_submit(current_time)  # Convert Morse to letter
        
        elif key in [ord('d'), ord('D')]:
            self._handle_delete(current_time)  # Delete last character
        
        elif key in [ord('t'), ord('T')]:
            self._handle_speak(current_time)   # Speak the decoded text
        
        elif key in [ord('b'), ord('B')]:
            self._handle_beep(current_time)    # Play Morse code sounds
        
        return True
    
    def _handle_reset(self, current_time: float):
        self.morse_sequence = ""
        self.invalid_sequence_detected = False
        self.ui_handler.show_notification("RESET: Current Morse sequence cleared.", current_time)
        print("RESET: Current Morse sequence cleared.")
        self.last_key_time = current_time
    
    def _handle_submit(self, current_time: float):
        if self.morse_sequence:
            letter = morse_to_letter(self.morse_sequence)
            if letter:
                self.decoded_text += letter
                message = f"SUBMITTED: '{self.morse_sequence}' decoded to '{letter}'"
                self.ui_handler.show_notification(message, current_time)
                print(message)
            else:
                message = f"ERROR: '{self.morse_sequence}' is not valid."
                self.ui_handler.show_notification(message, current_time, is_error=True)
                print(message)
            
            self.morse_sequence = ""
            self.invalid_sequence_detected = False
        else:
            message = "ERROR: No Morse sequence to submit."
            self.ui_handler.show_notification(message, current_time, is_error=True)
        
        self.last_key_time = current_time
    
    def _handle_delete(self, current_time: float):
        if self.decoded_text:
            deleted_char = self.decoded_text[-1]
            self.decoded_text = self.decoded_text[:-1]  # Remove last character
            message = f"DELETED: Removed '{deleted_char}'."
            self.ui_handler.show_notification(message, current_time)
            print(message)
        else:
            message = "ERROR: Nothing to delete."
            self.ui_handler.show_notification(message, current_time, is_error=True)
        
        self.last_key_time = current_time
    
    def _handle_speak(self, current_time: float):
        if self.decoded_text:
            self.ui_handler.show_notification("Speaking decoded text...", current_time)
            self.audio_manager.speak_text_non_blocking(self.decoded_text)
        else:
            message = "ERROR: No text to speak."
            self.ui_handler.show_notification(message, current_time, is_error=True)
        
        self.last_key_time = current_time
    
    def _handle_beep(self, current_time: float):
        if self.decoded_text:
            message = f"Playing Morse beeps for: '{self.decoded_text}'"
            self.ui_handler.show_notification(message, current_time)
            self.audio_manager.play_text_as_morse_non_blocking(self.decoded_text)
        else:
            message = "ERROR: No decoded text to play."
            self.ui_handler.show_notification(message, current_time, is_error=True)
        
        self.last_key_time = current_time
    
    def update_sequential_input(self, current_time: float):
        if not self.calibration_done:
            return  # Don't start until calibration is complete
        
        elapsed_time = current_time - self.sequence_start_time
        
        duration = INTERVAL_DURATION if self.is_interval_phase else self.current_symbol_data["duration"]
        
        if elapsed_time > duration:
            if self.is_interval_phase:
                self.sequence_index = (self.sequence_index + 1) % len(INPUT_SEQUENCE)
                self.current_symbol_data = INPUT_SEQUENCE[self.sequence_index]
                self.is_interval_phase = False  # Switch to symbol phase
            else:
                self.is_interval_phase = True
            
            self.sequence_start_time = current_time
    
    def process_eye_blink_input(self, avg_ear_s: float, current_time: float):
        # Only process blinks during symbol phases (not during rest periods)
        if not self.is_interval_phase and self.calibration_done:
            symbol = self.current_symbol_data["symbol"]  # Current active symbol
            
            blink_detected, new_symbol, blink_ended = self.vision_processor.detect_eye_blink(
                avg_ear_s, symbol, current_time)

            if new_symbol:
                if new_symbol == 'SPACE':  # Handle space separately
                    if self.decoded_text and self.decoded_text[-1] != " ":
                        self.decoded_text += " "
                        message = "SPACE added."
                        self.ui_handler.show_notification(message, current_time)
                        print(message)
                else:
                    temp_seq = self.morse_sequence + new_symbol

                    if is_valid_prefix(temp_seq):
                        self.morse_sequence = temp_seq
                        self.invalid_sequence_detected = False
                    else:
                        self.invalid_sequence_detected = True
                        message = f"ERROR: '{temp_seq}' invalid prefix."
                        self.ui_handler.show_notification(message, current_time, is_error=True)

    def process_light_blink_input(self, light_conf: float, current_time: float):
        if not self.is_interval_phase:
            symbol = self.current_symbol_data["symbol"]  # Current active symbol

            blink_detected, new_symbol, blink_ended = self.vision_processor.detect_light_blink(
                light_conf, symbol, current_time)

            if new_symbol:
                if new_symbol == 'SPACE':  # Handle space separately
                    if self.decoded_text and self.decoded_text[-1] != " ":
                        self.decoded_text += " "
                        message = "SPACE added."
                        self.ui_handler.show_notification(message, current_time)
                        print(message)
                else:
                    temp_seq = self.morse_sequence + new_symbol

                    if is_valid_prefix(temp_seq):
                        self.morse_sequence = temp_seq
                        self.invalid_sequence_detected = False
                    else:
                        self.invalid_sequence_detected = True
                        message = f"ERROR: '{temp_seq}' invalid prefix."
                        self.ui_handler.show_notification(message, current_time, is_error=True)

    def publish_frame(self, frame, current_time, status="", eye_readings=None):
        symbol = None
        duration = time_left = 0.0
        if self.calibration_done:
            if self.is_interval_phase:
                duration = INTERVAL_DURATION
            else:
                symbol = self.current_symbol_data["symbol"]
                duration = self.current_symbol_data["duration"]
            elapsed_time = current_time - self.sequence_start_time
            time_left = max(0.0, duration - elapsed_time)

        self.ui_handler.present(SessionFrame(
            frame=frame,
            morse_sequence=self.morse_sequence,
            decoded_text=self.decoded_text,
            invalid_sequence=self.invalid_sequence_detected,
            symbol=symbol,
            time_left=time_left,
            duration=duration,
            status=status,
            eye_readings=eye_readings,
        ))

    def run_light_blink(self):
        import cv2

        print("Blink-to-Morse detector initializing...")

        self.calibration_done = True

        try:
            if self.ui_handler.stop_requested.is_set():
                return
            self.initialize_camera()
            self.audio_manager.start_audio_threads()
            running = True
            while running and self.cap.isOpened() and not self.ui_handler.stop_requested.is_set():
                success, frame = self.cap.read()
                if not success:
                    break

                # Mirror the image so it feels more natural
                frame = cv2.flip(frame, 1)
                display_frame = frame.copy()
                current_time = time.time()  # Get current time for timing

                result_box = self.vision_processor.process_light_frame(display_frame)

                confidence = 0.0
                if result_box is not None:
                    confidence = float(result_box.conf[0])
                    x1, y1, x2, y2 = result_box.xyxy[0].cpu().numpy().astype(int)

                key = self.ui_handler.next_key()
                running = self.handle_key_input(key, current_time)
                if not running:
                    break  # User pressed 'Q' to quit

                self.update_sequential_input(current_time)  # Cycle through symbols
                self.process_light_blink_input(confidence, current_time)  # Detect blinks

                if result_box is not None:
                    cv2.rectangle(display_frame, (x1, y1), (x2, y2), (0, 255, 0), 2)
                self.publish_frame(display_frame, current_time)

        except KeyboardInterrupt:
            print("\nInterrupted by user")

        finally:
            self.cleanup()

    def run_eye_blink(self):
        import cv2

        print("Blink-to-Morse detector initializing...")
        print(f"Calibrating for {CALIBRATION_FRAMES} frames. Please keep your eyes open.")
        
        try:
            if self.ui_handler.stop_requested.is_set():
                return
            self.initialize_camera()
            self.audio_manager.start_audio_threads()
            running = True
            while running and self.cap.isOpened() and not self.ui_handler.stop_requested.is_set():
                success, frame = self.cap.read()
                if not success:
                    break
                
                # Mirror the image so it feels more natural
                frame = cv2.flip(frame, 1)
                display_frame = frame.copy()
                current_time = time.time()    # Get current time for timing
                
                results, left_ear_s, right_ear_s, avg_ear_s = self.vision_processor.process_eye_frame(frame)
                
                key = self.ui_handler.next_key()
                running = self.handle_key_input(key, current_time)
                if not running:
                    break  # User pressed 'Q' to quit
                
                # CALIBRATION PHASE: Learn user's normal eye openness
                if not self.calibration_done:
                    self.frame_count += 1
                    status_text = f"Keep both eyes open. {self.frame_count} of {CALIBRATION_FRAMES} frames."
                    
                    if self.frame_count >= CALIBRATION_FRAMES:
                        self.vision_processor.complete_calibration()
                        threshold = self.vision_processor.EAR_CLOSED_THRESHOLD
                        if threshold is None:
                            raise RuntimeError("No face was visible during calibration. Restart with your face in view.")
                        self.calibration_done = True
                        self.sequence_start_time = current_time
                        status_text = f"Calibration complete. Threshold {threshold:.2f}."
                        print(status_text)
                        print("Sequential Mode: DOT(3s)->1s GAP->DASH(3s)->1s GAP->SPACE(3s)->1s GAP. All with Both Eyes Blink.")
                
                else:
                    status_text = ""
                    self.update_sequential_input(current_time)  # Cycle through symbols
                    self.process_eye_blink_input(avg_ear_s, current_time)  # Detect blinks
                
                if results.multi_face_landmarks:
                    landmarks = results.multi_face_landmarks[0].landmark
                    self.vision_processor.draw_eye_landmarks(display_frame, landmarks)
                
                self.publish_frame(display_frame, current_time, status_text, (left_ear_s, right_ear_s))
                
        except KeyboardInterrupt:
            print("\nInterrupted by user")
        
        finally:
            self.cleanup()
    
    def cleanup(self):
        if self.cap:
            self.cap.release()  # Release camera
        
        self.audio_manager.cleanup()
        self.vision_processor.cleanup()
        
        print("Program terminated cleanly.")


def run_session(mode, ui_handler):
    app = MorseDecoderApp(ui_handler)
    if mode == "eye":
        app.run_eye_blink()
    else:
        app.run_light_blink()


if __name__ == "__main__":
    raise SystemExit(run_ui(run_session))
