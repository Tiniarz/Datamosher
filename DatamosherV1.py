import os
import subprocess
import sys
import re
import struct
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

def check_ffmpeg_dependencies():
    for binary in ["ffmpeg", "ffprobe"]:
        try:
            subprocess.run([binary, "-version"], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        except FileNotFoundError:
            messagebox.showerror("Critical Error", f"'{binary}' was not detected on your PC!\n\nEnsure FFmpeg is installed and added to your system's Environment Variables (PATH).")
            sys.exit(1)

def get_video_duration(video_path):
    cmd = [
        "ffprobe", "-v", "error", 
        "-show_entries", "format=duration", 
        "-of", "default=noprint_wrappers=1:nokey=1", 
        video_path
    ]
    try:
        result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, check=True)
        total_seconds = float(result.stdout.strip())
        return total_seconds
    except Exception:
        return 0.0

def convert_time_to_seconds(time_str):
    time_str = time_str.strip()
    match = re.match(r"^(\d+):([0-5]?\d)$", time_str)
    if match:
        mins, secs = map(int, match.groups())
        return (mins * 60) + secs
    return None

def convert_mp4_to_avi(input_path, output_avi, gop):
    cmd = [
        "ffmpeg", "-y", "-i", input_path,
        "-vcodec", "mpeg4", 
        "-g", str(gop), 
        "-qscale:v", "2", 
        output_avi
    ]
    try:
        subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True)
        return True
    except subprocess.CalledProcessError:
        return False

def parse_avi_index(data):
    keyframes = set()
    idx1_pos = data.rfind(b'idx1')
    if idx1_pos == -1:
        return keyframes
    
    idx_chunk_size = struct.unpack('<I', data[idx1_pos+4:idx1_pos+8])[0]
    entry_start = idx1_pos + 8
    entry_end = entry_start + idx_chunk_size
    
    current = entry_start
    movi_pos = data.find(b'movi')
    if movi_pos == -1:
        return keyframes
    movi_data_start = movi_pos + 4

    while current + 16 <= entry_end:
        chunk_id = data[current:current+4]
        flags = struct.unpack('<I', data[current+4:current+8])[0]
        offset = struct.unpack('<I', data[current+8:current+12])[0]
        
        if chunk_id == b'00dc':
            absolute_offset = movi_data_start + offset
            if flags & 0x10:
                keyframes.add(absolute_offset)
        current += 16
        
    return keyframes

def execute_native_mosh(avi_path, options):
    with open(avi_path, 'rb') as f:
        data = f.read()

    frame_marker = b'\x30\x30\x64\x63' 
    frame_indices = [m.start() for m in re.finditer(frame_marker, data)]
    total_frames = len(frame_indices)
    
    if total_frames == 0:
        return False

    keyframe_offsets = parse_avi_index(data)

    raw_start = int(options["start_sec"] * 30)
    start_frame = min(raw_start, total_frames - 1)
    if start_frame == 0:
        start_frame = 1

    end_frame = min(int(options["end_sec"] * 30), total_frames - 1)
    if start_frame >= end_frame:
        end_frame = total_frames - 1

    mode = options["mode"]
    output_bytes = bytearray()
    output_bytes.extend(data[:frame_indices[start_frame]])
    
    glide_saved_chunk = None
    glide_counter = 0
    stutter_buffer = []

    for i in range(start_frame, end_frame):
        start_pos = frame_indices[i]
        end_pos = frame_indices[i+1] if i+1 < total_frames else len(data)
        frame_chunk = data[start_pos:end_pos]
        frame_size = len(frame_chunk)

        if start_pos in keyframe_offsets:
            output_bytes.extend(frame_chunk)
            continue

        if mode == "AutoMosh":
            threshold = options["kill_frame_size"]
            if threshold == 0 or frame_size <= threshold:
                if frame_size > 8:
                    output_bytes.extend(frame_chunk[:8] + b'\x00' * (frame_size - 8))
                else:
                    output_bytes.extend(b'\x00' * frame_size)
            else:
                output_bytes.extend(frame_chunk)

        elif mode == "Classic":
            if i % (options["delta"] + 1) == 0:
                output_bytes.extend(frame_chunk)
            else:
                pass

        elif mode == "Glide":
            if glide_saved_chunk is None:
                glide_saved_chunk = frame_chunk
                
            if glide_counter < options["glide_intensity"]:
                output_bytes.extend(glide_saved_chunk)
                glide_counter += 1
            else:
                glide_saved_chunk = frame_chunk
                output_bytes.extend(frame_chunk)
                glide_counter = 0

        elif mode == "Repetition":
            stutter_buffer.append(frame_chunk)
            if len(stutter_buffer) > 12:
                stutter_buffer.pop(0)
            
            if i % 12 == 0 and len(stutter_buffer) == 12:
                for repeating_chunk in stutter_buffer:
                    output_bytes.extend(repeating_chunk)
            else:
                output_bytes.extend(frame_chunk)

        elif mode == "VoidMosh":
            if i % 2 == 0:
                output_bytes.extend(b'\x00' * frame_size)
            else:
                output_bytes.extend(frame_chunk)

        elif mode == "BloomPulse":
            if i % 5 == 0:
                output_bytes.extend(frame_chunk * 3)
            else:
                output_bytes.extend(frame_chunk)

        elif mode == "ReverseEcho":
            stutter_buffer.append(frame_chunk)
            if len(stutter_buffer) > 8:
                stutter_buffer.pop(0)
            for chunk in reversed(stutter_buffer):
                output_bytes.extend(chunk[:int(len(chunk) * 0.5)])

        elif mode == "BitCrush":
            crushed = bytearray(frame_chunk)
            for idx in range(len(crushed)):
                if idx % 4 == 0:
                    crushed[idx] = crushed[idx] ^ 0xFF
            output_bytes.extend(crushed)

    if end_frame < total_frames:
        output_bytes.extend(data[frame_indices[end_frame]:])

    with open(avi_path, 'wb') as f:
        f.write(output_bytes)
    return True

def fix_and_convert_to_mp4(input_avi, final_mp4, quality, speed_factor):
    setpts_val = 1.0 / speed_factor
    filter_str = f"setpts={setpts_val}*PTS"
    
    cmd = [
        "ffmpeg", "-y", "-i", input_avi,
        "-vf", filter_str,
        "-vcodec", "libx264", 
        "-crf", str(quality), 
        "-pix_fmt", "yuv420p", 
        final_mp4
    ]
    try:
        subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True)
        return True
    except subprocess.CalledProcessError:
        return False

class DatamosherGUI:
    def __init__(self, root):
        self.root = root
        self.root.title("Datamosher V1 Stable")
        self.root.geometry("900x550")
        self.root.configure(bg="#2d2d2d")
        
        self.style = ttk.Style()
        self.style.theme_use('default')
        self.style.configure('.', background='#2d2d2d', foreground='#ffffff', fieldbackground='#3d3d3d')
        self.style.configure('TLabel', background='#2d2d2d', foreground='#ffffff', font=('Segoe UI', 10))
        self.style.configure('TFrame', background='#2d2d2d')
        self.style.configure('TLabelframe', background='#2d2d2d', foreground='#ffffff')
        self.style.configure('TLabelframe.Label', background='#2d2d2d', foreground='#ffffff', font=('Segoe UI', 11, 'bold'))
        
        self.left_panel = ttk.Frame(self.root, width=320)
        self.left_panel.pack(side=tk.LEFT, fill=tk.Y, padx=20, pady=20)
        self.left_panel.pack_propagate(False)
        
        self.right_panel = ttk.Frame(self.root)
        self.right_panel.pack(side=tk.RIGHT, fill=tk.BOTH, expand=True, padx=20, pady=20)
        
        self.mode_var = tk.StringVar(value="AutoMosh")
        self.setup_left_panel()
        self.setup_right_panel()

    def setup_left_panel(self):
        lbl_modes = ttk.Label(self.left_panel, text="DATAMOSH METHODS", font=('Segoe UI', 12, 'bold'))
        lbl_modes.pack(anchor=tk.W, pady=(0, 15))
        
        features = [
            ("AutoMosh (Trendy)", "AutoMosh"),
            ("Classic (Traditional)", "Classic"),
            ("Glide (Gliding Pixels)", "Glide"),
            ("Repetition (Stutter)", "Repetition"),
            ("VoidMosh (Blackouts)", "VoidMosh"),
            ("BloomPulse (Ghosting)", "BloomPulse"),
            ("ReverseEcho (Feedback)", "ReverseEcho"),
            ("BitCrush (Data Corruption)", "BitCrush")
        ]
        
        for text, mode in features:
            rb = tk.Radiobutton(
                self.left_panel, text=text, value=mode, variable=self.mode_var,
                bg="#2d2d2d", fg="#ffffff", selectcolor="#3d3d3d", activebackground="#2d2d2d",
                activeforeground="#ffffff", font=('Segoe UI', 11), anchor=tk.W, justify=tk.LEFT,
                command=self.toggle_mode_inputs
            )
            rb.pack(fill=tk.X, pady=6)

    def setup_right_panel(self):
        lbl_title = ttk.Label(self.right_panel, text="DATAMOSHER V1 STABLE", font=('Segoe UI', 16, 'bold'))
        lbl_title.pack(anchor=tk.W, pady=(0, 20))
        
        io_frame = ttk.LabelFrame(self.right_panel, text=" File Configuration ")
        io_frame.pack(fill=tk.X, pady=(0, 15), ipady=5)
        
        ttk.Label(io_frame, text="Input MP4:").grid(row=0, column=0, padx=10, pady=10, sticky=tk.W)
        self.ent_path = tk.Entry(io_frame, bg="#3d3d3d", fg="#ffffff", insertbackground="#ffffff", bd=1, font=('Segoe UI', 10))
        self.ent_path.grid(row=0, column=1, padx=10, pady=10, sticky=tk.EW)
        io_frame.columnconfigure(1, weight=1)
        
        btn_browse = tk.Button(io_frame, text="Browse", bg="#4d4d4d", fg="#ffffff", activebackground="#5d5d5d", activeforeground="#ffffff", bd=0, padx=15, pady=3, font=('Segoe UI', 10), command=self.browse_file)
        btn_browse.grid(row=0, column=2, padx=10, pady=10)
        
        self.param_frame = ttk.LabelFrame(self.right_panel, text=" Parameter Settings ")
        self.param_frame.pack(fill=tk.BOTH, expand=True, pady=(0, 20), ipady=5)
        
        ttk.Label(self.param_frame, text="Start Window (MM:SS):").grid(row=0, column=0, padx=15, pady=8, sticky=tk.W)
        self.ent_start = tk.Entry(self.param_frame, bg="#3d3d3d", fg="#ffffff", insertbackground="#ffffff", bd=1, width=12)
        self.ent_start.insert(0, "00:00")
        self.ent_start.grid(row=0, column=1, padx=15, pady=8, sticky=tk.W)
        
        ttk.Label(self.param_frame, text="End Window (MM:SS):").grid(row=0, column=2, padx=15, pady=8, sticky=tk.W)
        self.ent_end = tk.Entry(self.param_frame, bg="#3d3d3d", fg="#ffffff", insertbackground="#ffffff", bd=1, width=12)
        self.ent_end.insert(0, "00:10")
        self.ent_end.grid(row=0, column=3, padx=15, pady=8, sticky=tk.W)
        
        ttk.Label(self.param_frame, text="Fixed Quality / CRF (1-51):").grid(row=1, column=0, padx=15, pady=8, sticky=tk.W)
        self.ent_quality = tk.Entry(self.param_frame, bg="#3d3d3d", fg="#ffffff", insertbackground="#ffffff", bd=1, width=12)
        self.ent_quality.insert(0, "23")
        self.ent_quality.grid(row=1, column=1, padx=15, pady=8, sticky=tk.W)
        
        ttk.Label(self.param_frame, text="Delta Value (Frame Skip):").grid(row=1, column=2, padx=15, pady=8, sticky=tk.W)
        self.ent_delta = tk.Entry(self.param_frame, bg="#3d3d3d", fg="#ffffff", insertbackground="#ffffff", bd=1, width=12)
        self.ent_delta.insert(0, "3")
        self.ent_delta.grid(row=1, column=3, padx=15, pady=8, sticky=tk.W)
        
        ttk.Label(self.param_frame, text="GOP / Keyframe Gap:").grid(row=2, column=0, padx=15, pady=8, sticky=tk.W)
        self.ent_gop = tk.Entry(self.param_frame, bg="#3d3d3d", fg="#ffffff", insertbackground="#ffffff", bd=1, width=12)
        self.ent_gop.insert(0, "45")
        self.ent_gop.grid(row=2, column=1, padx=15, pady=8, sticky=tk.W)
        
        self.lbl_dynamic = ttk.Label(self.param_frame, text="Kill Frame Size Limit:")
        self.lbl_dynamic.grid(row=2, column=2, padx=15, pady=8, sticky=tk.W)
        self.ent_dynamic = tk.Entry(self.param_frame, bg="#3d3d3d", fg="#ffffff", insertbackground="#ffffff", bd=1, width=12)
        self.ent_dynamic.insert(0, "0")
        self.ent_dynamic.grid(row=2, column=3, padx=15, pady=8, sticky=tk.W)
        
        self.btn_process = tk.Button(self.right_panel, text="APPLY FEATURES AND MOSH", bg="#5a5a5a", fg="#ffffff", activebackground="#707070", activeforeground="#ffffff", bd=0, font=('Segoe UI', 12, 'bold'), pady=10, command=self.process_video)
        self.btn_process.pack(fill=tk.X)

    def toggle_mode_inputs(self):
        mode = self.mode_var.get()
        self.lbl_dynamic.grid_forget()
        self.ent_dynamic.grid_forget()
        
        if mode == "AutoMosh":
            self.lbl_dynamic.configure(text="Kill Frame Size Limit:")
            self.lbl_dynamic.grid(row=2, column=2, padx=15, pady=8, sticky=tk.W)
            self.ent_dynamic.delete(0, tk.END)
            self.ent_dynamic.insert(0, "0")
            self.ent_dynamic.grid(row=2, column=3, padx=15, pady=8, sticky=tk.W)
        elif mode == "Glide":
            self.lbl_dynamic.configure(text="Glide Intensity:")
            self.lbl_dynamic.grid(row=2, column=2, padx=15, pady=8, sticky=tk.W)
            self.ent_dynamic.delete(0, tk.END)
            self.ent_dynamic.insert(0, "10")
            self.ent_dynamic.grid(row=2, column=3, padx=15, pady=8, sticky=tk.W)
        elif mode == "Classic":
            self.lbl_dynamic.configure(text="Playback Speed:")
            self.lbl_dynamic.grid(row=2, column=2, padx=15, pady=8, sticky=tk.W)
            self.ent_dynamic.delete(0, tk.END)
            self.ent_dynamic.insert(0, "1.0")
            self.ent_dynamic.grid(row=2, column=3, padx=15, pady=8, sticky=tk.W)

    def browse_file(self):
        file_path = filedialog.askopenfilename(filetypes=[("MP4 Videos", "*.mp4")])
        if file_path:
            self.ent_path.delete(0, tk.END)
            self.ent_path.insert(0, file_path)
            duration = get_video_duration(file_path)
            if duration > 0:
                mins = int(duration // 60)
                secs = int(duration % 60)
                self.ent_end.delete(0, tk.END)
                self.ent_end.insert(0, f"{mins:02d}:{secs:02d}")

    def process_video(self):
        input_video = self.ent_path.get().strip().strip('"').strip("'")
        if not input_video or not os.path.exists(input_video):
            messagebox.showerror("Error", "Invalid or missing input video file pathway.")
            return
            
        start_sec = convert_time_to_seconds(self.ent_start.get())
        end_sec = convert_time_to_seconds(self.ent_end.get())
        
        if start_sec is None or end_sec is None or start_sec > end_sec:
            messagebox.showerror("Error", "Invalid timeframe window format configuration rules.")
            return

        try:
            quality = int(self.ent_quality.get())
            delta_value = int(self.ent_delta.get())
            gop_value = int(self.ent_gop.get())
        except ValueError:
            messagebox.showerror("Error", "Base system metrics integers parsed incorrectly.")
            return

        selected_mode = self.mode_var.get()
        kill_frame_size = 0
        glide_intensity = 0
        classic_speed = 1.0
        
        dynamic_val = self.ent_dynamic.get()
        if selected_mode == "AutoMosh":
            kill_frame_size = int(dynamic_val or 0)
        elif selected_mode == "Glide":
            glide_intensity = int(dynamic_val or 10)
        elif selected_mode == "Classic":
            classic_speed = float(dynamic_val or 1.0)

        output_dir = os.path.dirname(os.path.abspath(input_video))
        temp_avi = os.path.join(output_dir, "temp_datamosh_holding.avi")
        final_mp4 = os.path.join(output_dir, "moshed_output.mp4")

        self.btn_process.configure(text="PROCESSING VIDEO... PLEASE WAIT", state=tk.DISABLED)
        self.root.update()

        mosh_options = {
            "mode": selected_mode,
            "kill_frame_size": kill_frame_size,
            "glide_intensity": glide_intensity,
            "start_sec": start_sec,
            "end_sec": end_sec,
            "delta": delta_value
        }

        success = False
        if convert_mp4_to_avi(input_video, temp_avi, gop_value):
            if execute_native_mosh(temp_avi, mosh_options):
                if fix_and_convert_to_mp4(temp_avi, final_mp4, quality, classic_speed):
                    success = True

        if os.path.exists(temp_avi):
            os.remove(temp_avi)

        self.btn_process.configure(text="APPLY FEATURES AND MOSH", state=tk.NORMAL)
        
        if success:
            messagebox.showinfo("Success", f"Moshed file exported to:\n{final_mp4}")
        else:
            messagebox.showerror("Error", "An anomaly transpired during frame reconstruction sequences.")

def main():
    check_ffmpeg_dependencies()
    root = tk.Tk()
    app = DatamosherGUI(root)
    root.mainloop()

if __name__ == "__main__":
    main()