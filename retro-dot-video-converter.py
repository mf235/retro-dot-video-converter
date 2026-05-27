# ================================================
# レトロ風ドット絵 動画変換ツール
#
# 【必須インストールコマンド】
# pip install --upgrade pillow numpy opencv-python tkinterdnd2 scikit-learn
#
# 【別途必要】
# ffmpeg をインストールして PATH に通してください。
# Windows なら winget install Gyan.FFmpeg など。
# ================================================

import os
import re
import math
import time
import shutil
import tempfile
import subprocess
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
try:
    from tkinterdnd2 import TkinterDnD, DND_FILES
    DND_AVAILABLE = True
except ImportError:
    TkinterDnD = None
    DND_FILES = None
    DND_AVAILABLE = False

import cv2
import numpy as np
from PIL import Image, ImageTk, ImageEnhance, ImageFilter
from sklearn.cluster import MiniBatchKMeans


VIDEO_EXTENSIONS = (".mp4", ".mov", ".avi", ".mkv", ".webm", ".m4v")
PREVIEW_MAX_SIZE = (300, 300)


def even_int(value: float) -> int:
    """動画エンコードで安全な偶数サイズに丸める。"""
    n = int(round(value))
    if n < 2:
        return 2
    return n if n % 2 == 0 else n + 1


def parse_size_from_label(label: str):
    """コンボ表示文字列から 640x360 のようなサイズを抜き出す。"""
    match = re.search(r"(\d+)x(\d+)", label)
    if not match:
        return None
    return int(match.group(1)), int(match.group(2))


def format_time(seconds: float) -> str:
    if seconds < 0:
        seconds = 0
    minutes = int(seconds // 60)
    secs = seconds - minutes * 60
    return f"{minutes:02d}:{secs:05.2f}"


class RetroVideoConverter:
    def __init__(self, root):
        self.root = root
        self.root.title("レトロ風ドット絵 動画変換ツール")

        window_width = 1280
        window_height = 950
        screen_width = root.winfo_screenwidth()
        screen_height = root.winfo_screenheight()
        x_coordinate = (screen_width // 2) - (window_width // 2)
        y_coordinate = (screen_height // 2) - (window_height // 2)
        root.geometry(f"{window_width}x{window_height}+{x_coordinate}+{y_coordinate}")

        self.video_path = None
        self.cap = None
        self.video_w = 0
        self.video_h = 0
        self.fps = 30.0
        self.frame_count = 0
        self.duration = 0.0
        self.current_frame_index = 0
        self.seek_after_id = None
        self.is_converting = False
        self.last_preview_settings_key = None
        self.input_img_tk = None
        self.output_img_tk = None

        self.size_map = {}

        main_frame = tk.Frame(root)
        main_frame.pack(fill="both", expand=True, padx=15, pady=10)

        # ====================== 左エリア ======================
        left_frame = tk.Frame(main_frame, width=680)
        left_frame.pack(side="left", fill="both", expand=True, padx=(0, 20))

        tk.Label(
            left_frame,
            text="レトロ風ドット絵 動画変換ツール",
            font=("メイリオ", 16, "bold"),
            anchor="w"
        ).pack(fill="x", pady=8)

        self.btn_select = tk.Button(
            left_frame,
            text="1. 動画を選択",
            command=self.select_video,
            width=25,
            height=2
        )
        self.btn_select.pack(anchor="w", pady=8)

        self.lbl_input = tk.Label(left_frame, text="入力動画: 未選択", anchor="w")
        self.lbl_input.pack(fill="x", pady=(0, 4))

        self.lbl_video_info = tk.Label(left_frame, text="動画情報: -", anchor="w", fg="#555555")
        self.lbl_video_info.pack(fill="x", pady=(0, 10))

        preview_area = tk.Frame(left_frame)
        preview_area.pack(pady=10)

        input_frame = tk.LabelFrame(preview_area, text="入力動画フレーム", font=("メイリオ", 9))
        input_frame.pack(side="left", padx=15)
        self.input_preview_frame = tk.Frame(input_frame, width=320, height=320, bg="#e8e8e8", relief="sunken", bd=3)
        self.input_preview_frame.pack(padx=10, pady=10)
        self.input_preview_frame.pack_propagate(False)
        self.lbl_input_preview = tk.Label(self.input_preview_frame, bg="#e8e8e8")
        self.lbl_input_preview.place(relx=0.5, rely=0.5, anchor="center")

        output_frame = tk.LabelFrame(preview_area, text="出力プレビュー", font=("メイリオ", 9))
        output_frame.pack(side="left", padx=15)
        self.output_preview_frame = tk.Frame(output_frame, width=320, height=320, bg="#e8e8e8", relief="sunken", bd=3)
        self.output_preview_frame.pack(padx=10, pady=10)
        self.output_preview_frame.pack_propagate(False)
        self.lbl_output_preview = tk.Label(self.output_preview_frame, bg="#e8e8e8")
        self.lbl_output_preview.place(relx=0.5, rely=0.5, anchor="center")

        # ====================== シークバー ======================
        seek_frame = tk.LabelFrame(left_frame, text="プレビュー位置", font=("メイリオ", 9))
        seek_frame.pack(fill="x", pady=(10, 5), padx=10)

        self.seek_var = tk.IntVar(value=0)
        self.seek_scale = tk.Scale(
            seek_frame,
            from_=0,
            to=0,
            orient="horizontal",
            variable=self.seek_var,
            command=self.on_seek_changed,
            length=600,
            state="disabled"
        )
        self.seek_scale.pack(fill="x", padx=10, pady=(6, 0))

        seek_bottom = tk.Frame(seek_frame)
        seek_bottom.pack(fill="x", padx=10, pady=(0, 8))

        self.lbl_time = tk.Label(seek_bottom, text="00:00.00 / 00:00.00", anchor="w")
        self.lbl_time.pack(side="left")

        self.btn_refresh_preview = tk.Button(
            seek_bottom,
            text="現在フレームを再プレビュー",
            command=self.update_preview_from_seek,
            state="disabled"
        )
        self.btn_refresh_preview.pack(side="right")

        # ====================== ボタンエリア ======================
        button_frame = tk.Frame(left_frame)
        button_frame.pack(fill="x", pady=20)

        filename_frame = tk.Frame(button_frame)
        filename_frame.pack(anchor="w", pady=(0, 10))
        tk.Label(filename_frame, text="出力ファイル名:", font=("メイリオ", 10)).pack(side="left")
        self.var_output_name = tk.StringVar(value="")
        tk.Entry(filename_frame, textvariable=self.var_output_name, width=45).pack(side="left", padx=(5, 5))
        tk.Label(filename_frame, text="※空なら 元ファイル名_retro.mp4", fg="#777777", font=("メイリオ", 9)).pack(side="left")

        self.btn_convert = tk.Button(
            button_frame,
            text="2. 動画をレトロ風に変換して保存！",
            command=self.convert_video,
            bg="#00FF00",
            fg="black",
            font=("メイリオ", 14, "bold"),
            width=30,
            height=4,
            state="disabled"
        )
        self.btn_convert.pack(side="left", padx=(0, 10))

        progress_frame = tk.Frame(left_frame)
        progress_frame.pack(fill="x", pady=(0, 5), padx=10)
        self.progress_var = tk.DoubleVar(value=0)
        self.progress_bar = ttk.Progressbar(progress_frame, variable=self.progress_var, maximum=100)
        self.progress_bar.pack(fill="x")
        self.lbl_progress = tk.Label(progress_frame, text="待機中", anchor="w", fg="#555555")
        self.lbl_progress.pack(fill="x", pady=(4, 0))

        # ====================== 右エリア ======================
        right_frame = tk.Frame(main_frame, width=560)
        right_frame.pack(side="right", fill="both", expand=True)

        tk.Label(right_frame, text="設定オプション", font=("メイリオ", 12, "bold"), anchor="w").pack(fill="x", pady=(10, 15))

        tk.Label(right_frame, text="プリセット", font=("メイリオ", 10), anchor="w").pack(fill="x", pady=(0, 2))
        self.preset_var = tk.StringVar(value="")
        self.preset_combo = ttk.Combobox(right_frame, textvariable=self.preset_var, state="readonly", width=38)
        self.preset_combo["values"] = ("", "PC98", "PC88", "MSX", "MSX2", "MSX2 インターレース")
        self.preset_combo.pack(anchor="w", pady=2)
        self.preset_combo.bind("<<ComboboxSelected>>", self.apply_preset)

        tk.Label(right_frame, text="出力サイズ", font=("メイリオ", 10), anchor="w").pack(fill="x", pady=(8, 0))
        self.size_var = tk.StringVar(value="動画を読み込んでください")
        self.size_combo = ttk.Combobox(right_frame, textvariable=self.size_var, state="disabled", width=38)
        self.size_combo["values"] = ("動画を読み込んでください",)
        self.size_combo.pack(anchor="w", pady=2)
        self.size_combo.bind("<<ComboboxSelected>>", self.on_size_selected)

        custom_frame = tk.Frame(right_frame)
        custom_frame.pack(anchor="w", pady=(2, 4))
        tk.Label(custom_frame, text="カスタム:", font=("メイリオ", 9)).pack(side="left")
        self.var_custom_w = tk.StringVar(value="")
        self.var_custom_h = tk.StringVar(value="")
        self.custom_w_entry = tk.Entry(custom_frame, textvariable=self.var_custom_w, width=7, justify="center", state="disabled")
        self.custom_w_entry.pack(side="left", padx=(5, 2))
        tk.Label(custom_frame, text="x", font=("メイリオ", 9)).pack(side="left")
        self.custom_h_entry = tk.Entry(custom_frame, textvariable=self.var_custom_h, width=7, justify="center", state="disabled")
        self.custom_h_entry.pack(side="left", padx=(2, 5))
        self.btn_custom_apply = tk.Button(custom_frame, text="適用", command=self.apply_custom_size, state="disabled")
        self.btn_custom_apply.pack(side="left")

        tk.Label(right_frame, text="色数", font=("メイリオ", 10), anchor="w").pack(fill="x", pady=(10, 0))
        self.colors_var = tk.StringVar(value="8")
        self.colors_combo = ttk.Combobox(right_frame, textvariable=self.colors_var, state="readonly", width=38)
        self.colors_combo["values"] = ("4", "8", "16", "32", "64", "128", "256", "512")
        self.colors_combo.pack(anchor="w", pady=2)
        self.colors_combo.bind("<<ComboboxSelected>>", self.on_setting_changed)

        tk.Label(right_frame, text="リサイズ方法", font=("メイリオ", 10), anchor="w").pack(fill="x", pady=(10, 0))
        self.resize_var = tk.StringVar(value="Bicubic (高品質・滑らか)")
        self.resize_combo = ttk.Combobox(right_frame, textvariable=self.resize_var, state="readonly", width=38)
        self.resize_combo["values"] = ("Nearest", "Bilinear", "Bicubic (高品質・滑らか)")
        self.resize_combo.pack(anchor="w", pady=2)
        self.resize_combo.bind("<<ComboboxSelected>>", self.on_setting_changed)

        self.var_pre_dot = tk.BooleanVar(value=True)
        self.chk_pre_dot = tk.Checkbutton(
            right_frame,
            text="✅ 事前ドット化（出力サイズへ先に縮小）",
            variable=self.var_pre_dot,
            font=("メイリオ", 10),
            anchor="w",
            command=self.on_setting_changed
        )
        self.chk_pre_dot.pack(fill="x", pady=(12, 4))

        self.var_outline = tk.BooleanVar(value=False)
        self.chk_outline = tk.Checkbutton(
            right_frame,
            text="✅ 輪郭線を黒に統一（レトロ風アウトライン強化）",
            variable=self.var_outline,
            font=("メイリオ", 10),
            anchor="w",
            command=self.on_setting_changed
        )
        self.chk_outline.pack(fill="x", pady=4)

        tk.Label(right_frame, text="ドット化強調（縮小→拡大）", font=("メイリオ", 10), anchor="w").pack(fill="x", pady=(12, 0))
        self.var_dot_boost = tk.StringVar(value="なし")
        self.dot_combo = ttk.Combobox(right_frame, textvariable=self.var_dot_boost, state="readonly", width=38)
        self.dot_combo["values"] = ("なし", "10%", "20%", "30%", "40%", "50%", "60%", "70%", "80%", "90%")
        self.dot_combo.pack(anchor="w", pady=2)
        self.dot_combo.bind("<<ComboboxSelected>>", self.on_setting_changed)
        tk.Label(right_frame, text="※小さい％ほど強いドット強調（10%が最強）", fg="red", font=("メイリオ", 9), anchor="w").pack(fill="x")

        self.var_anti_alias = tk.BooleanVar(value=True)
        self.chk_anti_alias = tk.Checkbutton(
            right_frame,
            text="✅ アンチエイリアス（輪郭部分のみ）",
            variable=self.var_anti_alias,
            font=("メイリオ", 10),
            anchor="w",
            command=self.on_setting_changed
        )
        self.chk_anti_alias.pack(fill="x", pady=6)

        self.var_chromatic = tk.BooleanVar(value=False)
        self.chk_chromatic = tk.Checkbutton(
            right_frame,
            text="✅ 色ずれ（色収差）効果を追加",
            variable=self.var_chromatic,
            font=("メイリオ", 10),
            anchor="w",
            command=self.on_setting_changed
        )
        self.chk_chromatic.pack(fill="x", pady=(4, 0))

        chromatic_frame = tk.Frame(right_frame)
        chromatic_frame.pack(fill="x", padx=25, pady=2)
        tk.Label(chromatic_frame, text="ずれの強度 (ピクセル):", font=("メイリオ", 9)).pack(side="left")
        self.var_chromatic_intensity = tk.StringVar(value="2")
        self.entry_chromatic = tk.Entry(chromatic_frame, textvariable=self.var_chromatic_intensity, width=5, justify="center")
        self.entry_chromatic.pack(side="left", padx=5)
        self.entry_chromatic.bind("<KeyRelease>", self.on_setting_changed)

        self.var_exp_skin_protect = tk.BooleanVar(value=False)
        self.chk_exp_skin_protect = tk.Checkbutton(
            right_frame,
            text="✅ 肌の黒ドット撲滅（事前バリア＋事後補正）",
            variable=self.var_exp_skin_protect,
            font=("メイリオ", 10),
            anchor="w",
            command=self.on_setting_changed
        )
        self.chk_exp_skin_protect.pack(fill="x", pady=(4, 0))

        param_frame = tk.Frame(right_frame)
        param_frame.pack(fill="x", padx=25, pady=2)
        tk.Label(param_frame, text="1:事前バリア [", font=("メイリオ", 9)).pack(side="left")
        self.var_pre_thresh = tk.StringVar(value="130")
        self.entry_pre_thresh = tk.Entry(param_frame, textvariable=self.var_pre_thresh, width=4, justify="center")
        self.entry_pre_thresh.pack(side="left")
        tk.Label(param_frame, text="]   2:事後補正 [", font=("メイリオ", 9)).pack(side="left")
        self.var_post_thresh = tk.StringVar(value="45")
        self.entry_post_thresh = tk.Entry(param_frame, textvariable=self.var_post_thresh, width=4, justify="center")
        self.entry_post_thresh.pack(side="left")
        tk.Label(param_frame, text="]", font=("メイリオ", 9)).pack(side="left")
        self.entry_pre_thresh.bind("<KeyRelease>", self.on_setting_changed)
        self.entry_post_thresh.bind("<KeyRelease>", self.on_setting_changed)

        notice = (
            "出力は mp4 固定。\n"
            "ffmpeg が使える場合、元動画の音声を自動コピーします。\n"
            "サイズ候補は動画の縦横比から自動生成します。"
        )
        tk.Label(right_frame, text=notice, fg="#555555", justify="left", anchor="w").pack(fill="x", pady=(18, 0))

        # ====================== Drag & Drop 設定 ======================
        if DND_AVAILABLE:
            self.root.drop_target_register(DND_FILES)
            self.root.dnd_bind("<<Drop>>", self.drop_video)

    # ====================== ファイル読み込み ======================
    def drop_video(self, event):
        files = self.root.tk.splitlist(event.data)
        valid_files = [f for f in files if os.path.isfile(f) and f.lower().endswith(VIDEO_EXTENSIONS)]
        if not valid_files:
            messagebox.showwarning("エラー", "有効な動画ファイルがありません")
            return
        self.load_video(valid_files[0])

    def select_video(self):
        path = filedialog.askopenfilename(
            title="変換したい動画を選んでね",
            filetypes=[("Video files", "*.mp4 *.mov *.avi *.mkv *.webm *.m4v"), ("All files", "*.*")]
        )
        if not path:
            return
        self.load_video(path)

    def load_video(self, path):
        if self.cap is not None:
            self.cap.release()

        cap = cv2.VideoCapture(path)
        if not cap.isOpened():
            messagebox.showerror("エラー", "動画を開けませんでした")
            return

        self.video_path = path
        self.cap = cap
        self.video_w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        self.video_h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        fps = float(cap.get(cv2.CAP_PROP_FPS))
        self.fps = fps if fps and fps > 0 else 30.0
        self.frame_count = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
        if self.frame_count <= 0:
            self.frame_count = 1
        self.duration = self.frame_count / self.fps
        self.current_frame_index = 0
        self.last_preview_settings_key = None

        self.lbl_input.config(text=f"入力動画: {os.path.basename(path)}")
        self.lbl_video_info.config(
            text=f"動画情報: {self.video_w}x{self.video_h} / {self.fps:.3f}fps / {self.frame_count}フレーム / {format_time(self.duration)}"
        )

        base, _ = os.path.splitext(os.path.basename(path))
        self.var_output_name.set(f"{base}_retro.mp4")

        self.generate_size_options()
        self.seek_scale.config(state="normal", from_=0, to=max(0, self.frame_count - 1))
        self.seek_var.set(0)
        self.btn_refresh_preview.config(state="normal")
        self.btn_convert.config(state="normal")
        self.progress_var.set(0)
        self.lbl_progress.config(text="待機中")
        self.update_preview_from_seek()

    # ====================== 出力サイズ候補 ======================
    def generate_size_options(self):
        if self.video_w <= 0 or self.video_h <= 0:
            return

        self.size_map = {}
        ratio = self.video_w / self.video_h

        canonical = [
            ("16:9", 16 / 9, [(256, 144), (320, 180), (480, 270), (640, 360), (960, 540), (1280, 720), (1920, 1080)]),
            ("4:3", 4 / 3, [(256, 192), (320, 240), (480, 360), (640, 480), (800, 600), (1024, 768), (1440, 1080)]),
            ("1:1", 1.0, [(128, 128), (256, 256), (320, 320), (480, 480), (640, 640), (1024, 1024)]),
            ("9:16", 9 / 16, [(144, 256), (180, 320), (270, 480), (360, 640), (540, 960), (720, 1280), (1080, 1920)]),
        ]

        aspect_name = "custom"
        sizes = None
        for name, target_ratio, candidates in canonical:
            if abs(ratio - target_ratio) / target_ratio <= 0.025:
                aspect_name = name
                sizes = candidates
                break

        labels = []
        keep_label = f"現状維持 - {even_int(self.video_w)}x{even_int(self.video_h)}"
        labels.append(keep_label)
        self.size_map[keep_label] = (even_int(self.video_w), even_int(self.video_h))

        if sizes is not None:
            named = []
            for w, h in sizes:
                if w <= 0 or h <= 0:
                    continue
                if aspect_name == "16:9":
                    label_name = self.size_label_for_width(w, {320: "レトロ小", 480: "レトロ中", 640: "標準", 1280: "高解像度", 1920: "フルHD"})
                elif aspect_name == "4:3":
                    label_name = self.size_label_for_width(w, {320: "レトロ小", 480: "レトロ中", 640: "標準", 1024: "高解像度"})
                elif aspect_name == "1:1":
                    label_name = self.size_label_for_width(w, {256: "レトロ小", 480: "レトロ中", 640: "標準", 1024: "高解像度"})
                else:
                    label_name = self.size_label_for_width(h, {320: "レトロ小", 480: "レトロ中", 640: "標準", 1280: "高解像度", 1920: "フルHD"})
                named.append((label_name, even_int(w), even_int(h)))

            for label_name, w, h in named:
                label = f"{label_name} - {w}x{h}"
                if label not in self.size_map:
                    labels.append(label)
                    self.size_map[label] = (w, h)
        else:
            for width in (256, 320, 480, 640, 960, 1280, 1920):
                height = even_int(width / ratio)
                w = even_int(width)
                label_name = self.size_label_for_width(width, {320: "レトロ小", 480: "レトロ中", 640: "標準", 1280: "高解像度", 1920: "大"})
                label = f"{label_name} - {w}x{height}"
                labels.append(label)
                self.size_map[label] = (w, height)

        labels.append("カスタム...")
        self.size_combo.config(state="readonly")
        self.size_combo["values"] = tuple(labels)
        default_label = labels[2] if len(labels) > 2 else labels[0]
        self.size_var.set(default_label)
        self.on_size_selected()

    @staticmethod
    def size_label_for_width(value, named_map):
        if value in named_map:
            return named_map[value]
        if value <= 320:
            return "小"
        if value <= 640:
            return "中"
        if value <= 1280:
            return "大"
        return "超大"

    def on_size_selected(self, event=None):
        is_custom = self.size_var.get() == "カスタム..."
        state = "normal" if is_custom else "disabled"
        self.custom_w_entry.config(state=state)
        self.custom_h_entry.config(state=state)
        self.btn_custom_apply.config(state=state)
        if not is_custom:
            size = self.get_selected_size()
            if size:
                self.var_custom_w.set(str(size[0]))
                self.var_custom_h.set(str(size[1]))
            self.update_preview_from_seek_delayed()

    def apply_custom_size(self):
        try:
            w = even_int(float(self.var_custom_w.get()))
            h = even_int(float(self.var_custom_h.get()))
        except ValueError:
            messagebox.showwarning("エラー", "カスタムサイズは数値で入力してね")
            return
        if w <= 0 or h <= 0:
            messagebox.showwarning("エラー", "カスタムサイズが不正です")
            return
        label = f"カスタム - {w}x{h}"
        self.size_map[label] = (w, h)
        values = list(self.size_combo["values"])
        if label not in values:
            insert_pos = max(0, len(values) - 1)
            values.insert(insert_pos, label)
            self.size_combo["values"] = tuple(values)
        self.size_var.set(label)
        self.on_size_selected()

    def get_selected_size(self):
        label = self.size_var.get()
        if label in self.size_map:
            return self.size_map[label]
        parsed = parse_size_from_label(label)
        if parsed:
            return even_int(parsed[0]), even_int(parsed[1])
        if self.video_w and self.video_h:
            return even_int(self.video_w), even_int(self.video_h)
        return None

    # ====================== プレビュー ======================
    def on_seek_changed(self, _value=None):
        if self.is_converting:
            return
        self.update_preview_from_seek_delayed()

    def on_setting_changed(self, event=None):
        if self.is_converting:
            return
        self.update_preview_from_seek_delayed()

    def update_preview_from_seek_delayed(self):
        if self.video_path is None:
            return
        if self.seek_after_id:
            self.root.after_cancel(self.seek_after_id)
        self.seek_after_id = self.root.after(180, self.update_preview_from_seek)

    def read_frame(self, frame_index):
        if self.cap is None:
            return None
        frame_index = max(0, min(int(frame_index), self.frame_count - 1))
        self.cap.set(cv2.CAP_PROP_POS_FRAMES, frame_index)
        ok, frame_bgr = self.cap.read()
        if not ok or frame_bgr is None:
            return None
        frame_rgb = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2RGB)
        return Image.fromarray(frame_rgb).convert("RGB")

    def update_preview_from_seek(self):
        if self.video_path is None:
            return
        self.seek_after_id = None
        idx = int(self.seek_var.get())
        self.current_frame_index = idx
        seconds = idx / self.fps if self.fps else 0
        self.lbl_time.config(text=f"{format_time(seconds)} / {format_time(self.duration)}  |  frame {idx + 1}/{self.frame_count}")

        src = self.read_frame(idx)
        if src is None:
            return

        out = self.process_frame(src)
        self.show_preview_image(src, self.lbl_input_preview, "input")
        self.show_preview_image(out, self.lbl_output_preview, "output")

    def show_preview_image(self, img, label_widget, target):
        preview = img.convert("RGB").copy()
        preview.thumbnail(PREVIEW_MAX_SIZE)
        tk_img = ImageTk.PhotoImage(preview)
        label_widget.config(image=tk_img)
        if target == "input":
            self.input_img_tk = tk_img
        else:
            self.output_img_tk = tk_img

    # ====================== プリセット ======================
    def apply_preset(self, event=None):
        preset = self.preset_var.get()
        if not preset:
            self.size_combo.config(state="readonly" if self.video_path else "disabled")
            self.colors_combo.config(state="readonly")
            self.update_preview_from_seek_delayed()
            return

        preset_map = {
            "PC98": ((640, 400), "16"),
            "PC88": ((640, 200), "8"),
            "MSX": ((256, 192), "16"),
            "MSX2": ((512, 212), "16"),
            "MSX2 インターレース": ((512, 424), "16"),
        }
        if preset in preset_map:
            size, colors = preset_map[preset]
            w, h = size
            label = f"{preset} - {w}x{h}"
            self.size_map[label] = (w, h)
            values = list(self.size_combo["values"])
            if label not in values:
                insert_pos = max(0, len(values) - 1)
                values.insert(insert_pos, label)
                self.size_combo["values"] = tuple(values)
            self.size_var.set(label)
            self.colors_var.set(colors)

        self.size_combo.config(state="disabled")
        self.colors_combo.config(state="disabled")
        self.update_preview_from_seek_delayed()

    # ====================== フレーム変換 ======================
    def get_resample_filter(self):
        resize_mode = self.resize_var.get()
        if "Bilinear" in resize_mode:
            return Image.BILINEAR
        if "Nearest" in resize_mode:
            return Image.NEAREST
        return Image.BICUBIC

    def process_frame(self, frame_img):
        img = frame_img.convert("RGB")
        target_size = self.get_selected_size()
        if target_size is None:
            target_size = img.size
        target_w, target_h = target_size
        resample = self.get_resample_filter()

        # 動画版では余白背景処理を削除。
        # 事前ドット化ON: 先に出力サイズへ縮小してから効果をかける。軽くてドット感が強い。
        # 事前ドット化OFF: 元解像度で効果をかけ、最後に出力サイズへ縮小する。少し滑らか寄り。
        if self.var_pre_dot.get() and img.size != (target_w, target_h):
            img = img.resize((target_w, target_h), resample)

        if self.var_outline.get():
            img = self.enhance_outline_weak(img)

        if self.var_anti_alias.get() and self.var_outline.get():
            img = self.apply_anti_alias_to_outline(img)

        if self.var_dot_boost.get() != "なし":
            img = self.dot_boost(img, self.var_dot_boost.get())

        if self.var_exp_skin_protect.get():
            edges = img.filter(ImageFilter.FIND_EDGES).convert("L")
            smoothed = img.filter(ImageFilter.SMOOTH_MORE).filter(ImageFilter.SMOOTH_MORE)
            edge_mask = edges.point(lambda x: 255 if x > 20 else 0, "1")
            img = Image.composite(img, smoothed, edge_mask)

        try:
            colors = int(self.colors_var.get())
        except ValueError:
            colors = 8

        try:
            pre_thresh = float(self.var_pre_thresh.get())
        except ValueError:
            pre_thresh = 130.0

        try:
            post_thresh = float(self.var_post_thresh.get())
        except ValueError:
            post_thresh = 45.0

        img = self.bayer_ordered_dither(
            img,
            colors,
            self.preset_var.get(),
            skin_protect=self.var_exp_skin_protect.get(),
            pre_thresh=pre_thresh,
            post_thresh=post_thresh
        ).convert("RGB")

        if self.var_chromatic.get():
            try:
                intensity = int(self.var_chromatic_intensity.get())
            except ValueError:
                intensity = 2
            img = self.apply_chromatic_aberration(img, intensity).convert("RGB")

        if img.size != (target_w, target_h):
            img = img.resize((target_w, target_h), resample)

        return img

    def enhance_outline_weak(self, img):
        edges = img.filter(ImageFilter.FIND_EDGES).convert("L")
        thick = edges.filter(ImageFilter.MaxFilter(3))
        mask = thick.point(lambda x: 0 if x < 120 else 255, "1")

        mask_array = np.array(mask)
        border = 2
        mask_array[0:border, :] = 0
        mask_array[-border:, :] = 0
        mask_array[:, 0:border] = 0
        mask_array[:, -border:] = 0
        mask = Image.fromarray(mask_array)
        black = Image.new("RGB", img.size, (0, 0, 0))
        result = Image.composite(black, img.convert("RGB"), mask)
        return result.convert("RGB")

    def apply_chromatic_aberration(self, img, intensity):
        if intensity <= 0:
            return img

        arr = np.array(img)
        shifted = np.copy(arr)

        if intensity >= arr.shape[1]:
            return img

        shifted[:, :-intensity, 0] = arr[:, intensity:, 0]
        shifted[:, intensity:, 2] = arr[:, :-intensity, 2]

        return Image.fromarray(shifted, mode=img.mode)

    def bayer_ordered_dither(self, img, colors=16, preset="", skin_protect=False, pre_thresh=130.0, post_thresh=45.0):
        img_rgb = img.convert("RGB")
        enhancer = ImageEnhance.Contrast(img_rgb)
        img_enhanced = enhancer.enhance(1.2)

        arr = np.array(img_enhanced, dtype=np.float32)

        bayer_matrix = np.array([
            [0, 8, 2, 10],
            [12, 4, 14, 6],
            [3, 11, 1, 9],
            [15, 7, 13, 5]
        ], dtype=np.float32)

        if preset == "PC88" or colors <= 8:
            amplitude = 128.0
        else:
            amplitude = 255.0 / colors * 2.0

        bayer = (bayer_matrix / 16.0 - 0.5) * amplitude

        h, w = arr.shape[:2]
        bayer = np.tile(bayer, (h // 4 + 1, w // 4 + 1))[:h, :w]
        bayer = np.expand_dims(bayer, axis=2)

        luminance = np.dot(arr[..., :3], [0.299, 0.587, 0.114])

        if skin_protect:
            lum_mask = np.clip((170.0 - luminance) / 60.0, 0.0, 1.0)
            lum_mask = np.expand_dims(lum_mask, axis=2)
            bayer = bayer * lum_mask

        noisy = arr + bayer

        if skin_protect:
            bright_mask = np.expand_dims(luminance > pre_thresh, axis=2)
            noisy = np.where(bright_mask, np.clip(noisy, pre_thresh, 255), noisy)

        noisy = np.clip(noisy, 0, 255)
        noisy_img = Image.fromarray(noisy.astype(np.uint8))

        result_img = None

        if preset == "PC88" or colors == 8:
            pal_img_tmp = Image.new("P", (1, 1))
            pal_img_tmp.putpalette([
                0, 0, 0,
                255, 0, 0,
                0, 255, 0,
                255, 255, 0,
                0, 0, 255,
                255, 0, 255,
                0, 255, 255,
                255, 255, 255,
            ] + [0] * 248 * 3)
            result_img = noisy_img.quantize(palette=pal_img_tmp, dither=0).convert("RGB")
        else:
            if preset in ["MSX2", "MSX2 インターレース"] or colors == 512:
                noisy_arr = np.array(noisy_img, dtype=np.float32)
                quantized_512 = np.round(noisy_arr / 36.428) * 36.428
                noisy_img = Image.fromarray(np.clip(quantized_512, 0, 255).astype(np.uint8))

            if colors >= 512:
                result_img = noisy_img.convert("RGB")
            else:
                if colors > 256:
                    img_array = np.array(noisy_img.convert("RGB")).reshape((-1, 3))
                    try:
                        kmeans = MiniBatchKMeans(n_clusters=colors, random_state=0, n_init="auto")
                    except TypeError:
                        kmeans = MiniBatchKMeans(n_clusters=colors, random_state=0)
                    labels = kmeans.fit_predict(img_array)
                    palette = kmeans.cluster_centers_.astype("uint8")
                    quantized = palette[labels].reshape(noisy_img.size[1], noisy_img.size[0], 3)
                    result_img = Image.fromarray(quantized).convert("RGB")
                else:
                    result_img = noisy_img.quantize(colors=colors, method=2, kmeans=0).convert("RGB")

        if skin_protect:
            q_arr = np.array(result_img)
            orig_arr = np.array(img_rgb)

            orig_lum = np.dot(orig_arr, [0.299, 0.587, 0.114])
            q_lum = np.dot(q_arr, [0.299, 0.587, 0.114])

            bad_mask = (orig_lum > 120) & (q_lum < post_thresh)

            if np.any(bad_mask):
                unique_colors = np.unique(q_arr.reshape(-1, 3), axis=0)
                safe_colors = [c for c in unique_colors if np.dot(c, [0.299, 0.587, 0.114]) >= post_thresh]

                if len(safe_colors) == 0:
                    safe_colors = [np.array([255, 255, 255], dtype=np.uint8)]

                safe_colors = np.array(safe_colors)
                bad_orig_colors = orig_arr[bad_mask]

                diff = bad_orig_colors[:, np.newaxis, :] - safe_colors[np.newaxis, :, :]
                dist = np.sum(diff ** 2, axis=2)
                best_color_idx = np.argmin(dist, axis=1)

                q_arr[bad_mask] = safe_colors[best_color_idx]
                result_img = Image.fromarray(q_arr)

        return result_img.convert("RGBA")

    def apply_anti_alias_to_outline(self, img):
        edges = img.filter(ImageFilter.FIND_EDGES).convert("L")
        mask = edges.point(lambda x: 0 if x < 100 else 255, "1")
        smoothed = img.filter(ImageFilter.SMOOTH)
        result = Image.composite(smoothed, img, mask)
        return result.convert("RGB")

    def dot_boost(self, img, percent_str):
        percent = int(percent_str.rstrip("%")) / 100.0
        w, h = img.size
        small_w = max(1, int(w * percent))
        small_h = max(1, int(h * percent))

        small = img.resize((small_w, small_h), Image.BICUBIC)
        boosted = small.resize((w, h), Image.NEAREST)
        return boosted.convert("RGB")

    # ====================== 動画変換 ======================
    def convert_video(self):
        if not self.video_path or self.is_converting:
            return

        ffmpeg_path = shutil.which("ffmpeg")
        if not ffmpeg_path:
            messagebox.showerror(
                "ffmpeg が見つかりません",
                "ffmpeg が PATH にありません。\nffmpeg をインストールしてから再実行してね。"
            )
            return

        output_name = self.var_output_name.get().strip()
        if not output_name:
            base, _ = os.path.splitext(os.path.basename(self.video_path))
            output_name = f"{base}_retro.mp4"
        if not output_name.lower().endswith(".mp4"):
            output_name += ".mp4"

        output_path = os.path.join(os.path.dirname(os.path.abspath(self.video_path)), output_name)
        if os.path.abspath(output_path) == os.path.abspath(self.video_path):
            messagebox.showwarning("エラー", "入力動画と同じファイル名にはできません")
            return

        target_size = self.get_selected_size()
        if target_size is None:
            messagebox.showwarning("エラー", "出力サイズが不正です")
            return
        target_w, target_h = target_size

        self.is_converting = True
        self.btn_convert.config(state="disabled")
        self.btn_select.config(state="disabled")
        self.btn_refresh_preview.config(state="disabled")
        self.progress_var.set(0)
        self.lbl_progress.config(text="変換準備中...")
        self.root.update_idletasks()

        tmp_dir = tempfile.mkdtemp(prefix="retro_video_")
        temp_video_path = os.path.join(tmp_dir, "video_only.mp4")

        try:
            cap = cv2.VideoCapture(self.video_path)
            if not cap.isOpened():
                raise RuntimeError("動画を開けませんでした")

            fourcc = cv2.VideoWriter_fourcc(*"mp4v")
            writer = cv2.VideoWriter(temp_video_path, fourcc, self.fps, (target_w, target_h))
            if not writer.isOpened():
                raise RuntimeError("一時動画の書き出しを開始できませんでした")

            total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
            if total <= 0:
                total = self.frame_count

            start_time = time.time()
            frame_idx = 0
            last_ui_update = 0.0

            while True:
                ok, frame_bgr = cap.read()
                if not ok or frame_bgr is None:
                    break

                frame_rgb = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2RGB)
                pil_img = Image.fromarray(frame_rgb).convert("RGB")
                processed = self.process_frame(pil_img).convert("RGB")
                out_rgb = np.array(processed)
                out_bgr = cv2.cvtColor(out_rgb, cv2.COLOR_RGB2BGR)
                writer.write(out_bgr)

                frame_idx += 1
                now = time.time()
                if now - last_ui_update >= 0.12 or frame_idx == total:
                    percent = min(100.0, frame_idx / max(1, total) * 100.0)
                    elapsed = now - start_time
                    fps_proc = frame_idx / elapsed if elapsed > 0 else 0
                    self.progress_var.set(percent)
                    self.lbl_progress.config(text=f"変換中... {frame_idx}/{total} フレーム ({percent:.1f}%) / {fps_proc:.1f} fps")
                    self.root.update_idletasks()
                    last_ui_update = now

            cap.release()
            writer.release()

            self.lbl_progress.config(text="音声を結合中... ffmpeg 実行")
            self.root.update_idletasks()

            # temp_video_path の映像 + 元動画の音声を結合。音声なしでも -map 1:a? で通す。
            ffmpeg_cmd = [
                ffmpeg_path,
                "-y",
                "-i", temp_video_path,
                "-i", self.video_path,
                "-map", "0:v:0",
                "-map", "1:a?",
                "-c:v", "libx264",
                "-pix_fmt", "yuv420p",
                "-crf", "18",
                "-preset", "medium",
                "-c:a", "aac",
                "-b:a", "192k",
                "-shortest",
                output_path,
            ]
            result = subprocess.run(ffmpeg_cmd, capture_output=True, text=True)
            if result.returncode != 0:
                # libx264 が使えない環境向けに、映像コピーで再試行する。
                fallback_cmd = [
                    ffmpeg_path,
                    "-y",
                    "-i", temp_video_path,
                    "-i", self.video_path,
                    "-map", "0:v:0",
                    "-map", "1:a?",
                    "-c:v", "copy",
                    "-c:a", "aac",
                    "-b:a", "192k",
                    "-shortest",
                    output_path,
                ]
                fallback = subprocess.run(fallback_cmd, capture_output=True, text=True)
                if fallback.returncode != 0:
                    raise RuntimeError("ffmpeg での結合に失敗しました:\n" + fallback.stderr[-1200:])

            self.progress_var.set(100)
            self.lbl_progress.config(text=f"完了: {output_path}")
            messagebox.showinfo("変換完了", f"動画変換が完了しました！\n{output_path}")

        except Exception as e:
            messagebox.showerror("エラー", f"動画変換中にエラーが発生しました:\n{str(e)}")
            self.lbl_progress.config(text="エラーで停止")
        finally:
            try:
                shutil.rmtree(tmp_dir, ignore_errors=True)
            except Exception:
                pass
            self.is_converting = False
            self.btn_select.config(state="normal")
            self.btn_refresh_preview.config(state="normal" if self.video_path else "disabled")
            self.btn_convert.config(state="normal" if self.video_path else "disabled")
            self.root.update_idletasks()

    def __del__(self):
        try:
            if self.cap is not None:
                self.cap.release()
        except Exception:
            pass


if __name__ == "__main__":
    root = TkinterDnD.Tk() if DND_AVAILABLE else tk.Tk()
    app = RetroVideoConverter(root)
    root.mainloop()
