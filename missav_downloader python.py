# coding: utf-8
"""
범용 M3U8 비디오 다운로더 (yt-dlp 사용)
-----------------------------------------
이 프로그램은 지정된 웹 페이지에서 M3U8 비디오 링크를 분석하고,
yt-dlp를 사용하여 해당 비디오를 다운로드하는 GUI 애플리케이션입니다.
자동 클립보드 감지 및 URL 기반 파일명 생성 기능을 포함합니다.

주요 기능:
- Tkinter 기반의 GUI
- 웹 페이지 URL로부터 M3U8 링크 자동 분석 (Selenium 사용)
- MissAV 사이트 특화 난독화 해제 로직 포함
- 클립보드 감지를 통한 자동 다운로드 (missav.ws URL 대상)
- URL 경로에서 파일명 자동 추출 (예: .../abcd-123-suffix -> abcd-123.mp4)
- yt-dlp를 이용한 비디오 다운로드
- 동시 다운로드 수 제한 (기본 2개) 및 다운로드 대기열 관리
- 다운로드 진행률 그래픽 바 표시
- 임시 폴더에 다운로드 후 최종 위치로 이동 및 파일명 정리
"""

import tkinter as tk
from tkinter import ttk # Themed Tkinter widgets (Progressbar)
from tkinter import filedialog, scrolledtext, messagebox
import requests
# from bs4 import BeautifulSoup # URL 기반 파일명 사용으로 현재 필수 아님
import selenium
from selenium import webdriver
from selenium.webdriver.chrome.service import Service as ChromeService
from selenium.webdriver.chrome.options import Options
from webdriver_manager.chrome import ChromeDriverManager # ChromeDriver 자동 관리
import os
import threading
import logging
import re
import subprocess
from urllib.parse import urlparse # URL 파싱용
import time
import collections # deque 사용 (다운로드 대기열)
import shutil # 파일 이동용

# --- 로깅 설정 ---
LOG_FILENAME = 'debug_downloader_m3u8_v6.3.8.txt'
logging.basicConfig(
    filename=LOG_FILENAME,
    level=logging.DEBUG, # 개발 중에는 DEBUG, 배포 시 INFO 등으로 변경 가능
    format='%(asctime)s - %(levelname)s - %(threadName)s - %(funcName)s - %(message)s', # 함수명 추가
    filemode='w' # 실행 시마다 로그 파일 덮어쓰기
)

# --- 전역 상수 ---
USER_AGENT = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
REQUEST_HEADERS = { # 웹 요청 시 사용할 기본 헤더
    'User-Agent': USER_AGENT,
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.9',
    'Accept-Language': 'ko-KR,ko;q=0.9,en-US;q=0.8,en;q=0.7',
    'Accept-Encoding': 'gzip, deflate, br', # requests가 자동으로 처리
    'Connection': 'keep-alive',
    'DNT': '1', # Do Not Track
    'Upgrade-Insecure-Requests': '1'
}
TEMP_DOWNLOAD_SUBDIR = "_temp_downloads" # 임시 다운로드 하위 폴더명
MAX_CONCURRENT_DOWNLOADS_DEFAULT = 2 # 최대 동시 다운로드 수
CLIPBOARD_CHECK_INTERVAL_MS = 2000 # 클립보드 확인 간격 (밀리초)
SELENIUM_JS_WAIT_TIME_S = 8 # Selenium 페이지 로드 후 JavaScript 실행 대기 시간 (초)
REQUEST_TIMEOUT_S = 20 # requests 사용 시 타임아웃 (초)

class VideoDownloaderApp:
    """
    M3U8 비디오 다운로더 GUI 애플리케이션 클래스.
    """
    def __init__(self, root_window):
        """
        애플리케이션 초기화 및 UI 구성.
        """
        self.root = root_window
        self.root.title(f"M3U8 다운로더 (yt-dlp, URL파일명) v6.3.8") # 버전 명시
        self.root.geometry("780x780") # UI 요소 크기 고려하여 조정

        # 상태 변수 초기화
        self.is_processing_auto = False # 자동 클립보드 처리 진행 중 플래그
        self.is_manual_analyzing = False # 수동 M3U8 분석 진행 중 플래그
        self.last_clipboard_content = ""
        self.clipboard_monitoring_active = False
        self.after_id_clipboard_check = None # Tkinter 'after' 이벤트 ID 저장용

        # 다운로드 관리 변수
        self.download_queue = collections.deque()
        self.active_downloads = 0
        self.download_lock = threading.Lock() # 공유 자원 접근 동기화용
        self.MAX_CONCURRENT_DOWNLOADS = MAX_CONCURRENT_DOWNLOADS_DEFAULT

        # UI 요소 생성 및 배치
        self._setup_ui()

        logging.info("애플리케이션 시작됨 (v6.3.8)")
        self.log_message(f"디버그 로그는 '{LOG_FILENAME}' 파일에 저장됩니다.")
        self.log_message("yt-dlp와 FFmpeg가 시스템 PATH에 설치되어 있어야 합니다.")
        self.log_message("v6.3.8: 코드 정리 및 주석 추가, 가독성 향상.")
        self.update_global_ui_state() # 초기 UI 상태 설정

    def _setup_ui(self):
        """UI 요소들을 생성하고 배치합니다."""
        frame_controls = ttk.Frame(self.root, padding="10")
        frame_controls.grid(row=0, column=0, sticky="ew", columnspan=3) # 컨트롤 영역 프레임
        
        # URL 입력
        ttk.Label(frame_controls, text="페이지 URL:").grid(row=0, column=0, padx=5, pady=5, sticky="w")
        self.url_entry = ttk.Entry(frame_controls, width=70)
        self.url_entry.grid(row=0, column=1, padx=5, pady=5, sticky="ew")
        self.url_entry.insert(0, "https://missav.ws/ko/...") # 예시 URL

        # 저장 폴더 선택
        ttk.Label(frame_controls, text="저장 폴더:").grid(row=1, column=0, padx=5, pady=5, sticky="w")
        self.folder_path_var = tk.StringVar()
        self.folder_entry = ttk.Entry(frame_controls, textvariable=self.folder_path_var, width=55, state='readonly')
        self.folder_entry.grid(row=1, column=1, padx=5, pady=5, sticky="ew")
        self.browse_button = ttk.Button(frame_controls, text="폴더 선택", command=self.browse_folder)
        self.browse_button.grid(row=1, column=2, padx=5, pady=5, sticky="e")

        # 저장 파일명
        ttk.Label(frame_controls, text="저장 파일명 (MP4, URL기반 자동생성):").grid(row=2, column=0, padx=5, pady=5, sticky="w")
        self.filename_entry = ttk.Entry(frame_controls, width=70)
        self.filename_entry.grid(row=2, column=1, padx=5, pady=5, sticky="ew")

        # 분석 및 다운로드 버튼
        self.analyze_button = ttk.Button(frame_controls, text="M3U8 링크 분석 (수동)", command=self.start_analysis_thread)
        self.analyze_button.grid(row=3, column=0, padx=5, pady=10, sticky="ew")
        self.download_button = ttk.Button(frame_controls, text="선택된 M3U8 다운로드", command=self.start_manual_download)
        self.download_button.grid(row=3, column=1, padx=5, pady=10, sticky="ew", columnspan=2) # columnspan 조정 가능

        # M3U8 링크 리스트 박스
        ttk.Label(frame_controls, text="찾은 M3U8 링크:").grid(row=4, column=0, padx=5, pady=(10,0), sticky="nw") # pady 위쪽 간격 추가
        self.link_listbox = tk.Listbox(frame_controls, selectmode=tk.SINGLE, width=80, height=6)
        self.link_listbox.grid(row=5, column=0, columnspan=3, padx=5, pady=5, sticky="ew") # columnspan=3으로 변경

        # 자동 다운로드 체크박스
        self.auto_download_var = tk.BooleanVar()
        self.auto_download_checkbutton = ttk.Checkbutton(frame_controls, text="자동 다운로드 활성화 (missav.ws URL 복사 시)",
                                                        variable=self.auto_download_var, command=self.toggle_clipboard_monitoring)
        self.auto_download_checkbutton.grid(row=6, column=0, columnspan=3, padx=5, pady=5, sticky="w")
        
        frame_controls.grid_columnconfigure(1, weight=1) # URL, 파일명 입력창 확장

        # 다운로드 진행률 바 영역
        progress_section_label = ttk.Label(self.root, text="다운로드 진행 상황:", padding=(10,5,0,0))
        progress_section_label.grid(row=1, column=0, sticky="w", columnspan=3)
        self.progress_area_frame = ttk.Frame(self.root, padding=(10,0,10,5))
        self.progress_area_frame.grid(row=2, column=0, columnspan=3, sticky="ew", padx=5, pady=2)
        
        self.progress_elements = []
        for i in range(self.MAX_CONCURRENT_DOWNLOADS):
            slot_frame = ttk.Frame(self.progress_area_frame)
            slot_frame.grid(row=i, column=0, sticky="ew", pady=3) # pady로 슬롯 간 간격
            self.progress_area_frame.grid_columnconfigure(0, weight=1) # 슬롯 프레임 확장

            label = ttk.Label(slot_frame, text=f"슬롯 {i+1}: 대기 중", anchor="w", width=70) # 너비 조정
            label.pack(side="left", fill="x", expand=True, padx=(0,5))
            
            p_bar = ttk.Progressbar(slot_frame, orient="horizontal", length=100, mode="determinate") # 너비는 pack에서 결정
            p_bar.pack(side="left", fill="x", expand=True)
            
            self.progress_elements.append({
                'bar': p_bar, 
                'label': label, 
                'active_file_key': None, # 현재 이 슬롯에서 처리 중인 파일 식별자 (예: 최종 저장 경로)
                '_filename_for_display': '' # UI 표시에 사용할 짧은 파일명
            })

        # 상태 및 로그 창
        log_section_label = ttk.Label(self.root, text="상태 및 로그:", padding=(10,10,0,0))
        log_section_label.grid(row=3, column=0, sticky="w", columnspan=3)
        self.status_text = scrolledtext.ScrolledText(self.root, width=80, height=12, state=tk.DISABLED, wrap=tk.WORD)
        self.status_text.grid(row=4, column=0, columnspan=3, padx=10, pady=5, sticky="nsew")

        # 전체 창 확장 설정
        self.root.grid_rowconfigure(4, weight=1) # 로그창이 세로로 확장되도록
        self.root.grid_columnconfigure(0, weight=1) # 전체적으로 가로 확장 (columnspan=3 사용한 위젯들에 의해)


    def log_message(self, message, level="INFO"):
        """GUI의 상태창 및 파일에 로그 메시지를 기록합니다."""
        if level == "PROGRESS": # 진행률 이벤트는 별도 UI로 처리, 파일 로그에만 기록
            logging.debug(f"PROGRESS_EVENT: {message}")
            return 
        
        if not hasattr(self, 'status_text') or not self.status_text.winfo_exists():
            # UI 요소가 아직 없거나 파괴된 경우 (프로그램 종료 시 등)
            print(f"임시 출력(status_text 없음) [{level}]: {message}")
            logging.error("log_message 호출 시 self.status_text 없음 또는 파괴됨!")
            return

        self.status_text.config(state=tk.NORMAL)
        prefix = f"[{level}] "
        if level == "DEBUG_YT": 
            prefix = "" # yt-dlp의 순수 출력은 레벨 접두사 없이
            
        self.status_text.insert(tk.END, f"{prefix}{message}\n")
        self.status_text.see(tk.END) # 항상 마지막 줄로 스크롤
        self.status_text.config(state=tk.DISABLED)
        
        # 파일 로깅
        if level == "ERROR": logging.error(message)
        elif level == "WARNING": logging.warning(message)
        else: logging.info(message)


    def update_ui_specific_progress(self, slot_index, percent_float, filename, size_str):
        """지정된 다운로드 슬롯의 진행률 바와 레이블을 업데이트합니다."""
        if 0 <= slot_index < len(self.progress_elements):
            slot = self.progress_elements[slot_index]
            slot['bar']['value'] = percent_float
            
            # 파일명이 너무 길 경우 축약 (예: 앞 20자 ... 뒤 10자)
            display_name_short = filename
            if len(filename) > 45: # 레이블 공간 고려하여 길이 조절
                display_name_short = filename[:25] + "..." + filename[-15:]
            
            slot['label'].config(text=f"{display_name_short} ({size_str})")
            logging.debug(f"UI_PROGRESS Slot {slot_index}: {filename} - {percent_float:.1f}% ({size_str})")


    def clear_progress_slot(self, slot_index, status_message, success=False):
        """지정된 다운로드 슬롯의 정보를 초기화하고 상태 메시지를 표시합니다."""
        if 0 <= slot_index < len(self.progress_elements):
            slot = self.progress_elements[slot_index]
            filename_done = slot['_filename_for_display'] 
            
            label_text_filename = filename_done
            if filename_done and len(filename_done) > 35: # 완료/실패 시 파일명도 길이 조절
                 label_text_filename = filename_done[:20] + "..." + filename_done[-10:]
            elif not filename_done: # 파일명이 없는 경우 (이론상 드묾)
                label_text_filename = f"슬롯 {slot_index+1}"

            slot['label'].config(text=f"{label_text_filename}: {status_message}")
            slot['bar']['value'] = 100 if success else 0 # 성공 시 100%, 아니면 0
            slot['active_file_key'] = None # 슬롯을 다시 사용 가능하도록 표시
            slot['_filename_for_display'] = '' # 저장된 표시용 파일명 초기화
            logging.debug(f"UI_PROGRESS Slot {slot_index} cleared. Filename: {filename_done}, Status: {status_message}")


    def browse_folder(self):
        """저장 폴더 선택 대화상자를 열고 선택된 경로를 UI에 반영합니다."""
        folder_selected = filedialog.askdirectory()
        if folder_selected: 
            self.folder_path_var.set(folder_selected)
            self.log_message(f"저장 폴더 선택: {folder_selected}")


    def update_global_ui_state(self):
        """애플리케이션의 전반적인 작업 상태에 따라 UI 요소들의 활성화 상태를 조절합니다."""
        with self.download_lock: 
            is_globally_busy = (self.active_downloads > 0 or 
                                len(self.download_queue) > 0 or 
                                self.is_processing_auto or 
                                self.is_manual_analyzing)
        
        state_to_set = tk.DISABLED if is_globally_busy else tk.NORMAL
        
        # 토글할 위젯 목록
        widgets_to_toggle = [
            getattr(self, name, None) for name in [
                "analyze_button", "browse_button", "url_entry", 
                "filename_entry", "auto_download_checkbutton", "download_button"
            ]
        ]
        
        for widget in widgets_to_toggle:
            if widget and widget.winfo_exists(): # 위젯이 실제로 생성되었고 파괴되지 않았는지 확인
                widget.config(state=state_to_set)
        
        # 다운로드 버튼은 특별 조건 추가 (바쁘지 않고, 링크가 있고, 선택되었을 때만 활성화)
        if hasattr(self, 'download_button') and self.download_button.winfo_exists():
            if not is_globally_busy and \
               hasattr(self, 'link_listbox') and self.link_listbox.winfo_exists() and \
               self.link_listbox.size() > 0 and self.link_listbox.curselection():
                self.download_button.config(state=tk.NORMAL)
            else:
                self.download_button.config(state=tk.DISABLED)


    def sanitize_filename(self, filename_to_sanitize): # 매개변수명 변경
        """파일명으로 사용할 수 없는 문자를 제거하고 적절히 정리합니다."""
        if not isinstance(filename_to_sanitize, str): # 방어 코드
            logging.warning(f"sanitize_filename에 문자열이 아닌 값 전달됨: {type(filename_to_sanitize)}")
            return f"invalid_input_{int(time.time())}"

        # 윈도우 파일명 금지 문자: \ / : * ? " < > |
        filename = re.sub(r'[\\/*?:"<>|]', "", filename_to_sanitize)
        filename = filename.strip() # 양쪽 공백 제거
        filename = re.sub(r'\s+', '_', filename) # 여러 공백 및 일반 공백을 밑줄로 변경 (선택 사항)
        
        # 파일명이 너무 길어지는 것을 방지 (예: 200자 제한)
        # 확장자(.mp4 등)는 별도 처리하지 않으므로, 순수 파일명에 대한 길이 제한임
        filename = filename[:200] 
        
        if not filename: # 모든 문자가 제거되었거나 원래 비어있던 경우
            return "video_" + str(int(time.time()))
        return filename


    def extract_filename_from_url(self, page_url):
        """
        페이지 URL에서 비디오 ID와 유사한 부분을 추출하여 파일명으로 사용합니다.
        예: https://missav.ws/ko/mkmp-634-uncensored-leak -> mkmp-634
        """
        try:
            parsed_url = urlparse(page_url)
            # 경로를 '/'로 분리하고, 비어있지 않은 요소만 필터링
            path_segments = [segment for segment in parsed_url.path.split('/') if segment]

            if not path_segments:
                self.log_message(f"URL에 경로 정보가 없어 파일명 추출 불가: {page_url}", "WARNING")
                return f"url_no_path_{int(time.time())}"

            # 다양한 패턴을 시도하여 파일명 후보 추출
            filename_candidates = []

            # 패턴 1: 경로의 마지막 요소에서 XXX-NNN 또는 XXXX-NNN 형태 추출 (가장 일반적)
            last_segment = path_segments[-1]
            cleaned_last_segment = last_segment
            common_suffixes = ["-uncensored-leak", "-uncensored", "-leak", "-subtitle", "-sub", "-hd", "-fhd"]
            for suffix in common_suffixes:
                if cleaned_last_segment.lower().endswith(suffix.lower()): # 대소문자 무시
                    cleaned_last_segment = cleaned_last_segment[:-len(suffix)]
            
            # XXX-NNN (X는 영문자 또는 숫자, N은 숫자)
            # (?:-[a-zA-Z0-9]+)* 는 중간에 추가적인 하이픈과 문자열 허용 (예: abc-def-123)
            match_id_pattern = re.fullmatch(r'([a-zA-Z0-9]+(?:-[a-zA-Z0-9]+)*-\d+)', cleaned_last_segment, re.IGNORECASE)
            if match_id_pattern:
                filename_candidates.append(match_id_pattern.group(1))
            
            # 패턴 2: cleaned_last_segment 자체가 의미있는 ID일 수 있음 (예: stars123, javplayer)
            # 단, 너무 짧거나 단순한 국가코드 등은 제외
            if re.fullmatch(r'[a-zA-Z0-9]{4,}', cleaned_last_segment) and \
               not (len(cleaned_last_segment) <= 3 and cleaned_last_segment.lower() in ['ko','en','jp','cn','us']):
                filename_candidates.append(cleaned_last_segment)


            # 패턴 3: 경로 중간에 특징적인 ID 패턴이 있는 경우 (예: /ko/javmodel/abcd-123/...)
            # 이 부분은 사이트 구조에 따라 더 복잡한 로직 필요 가능. 여기서는 간단히 처리.
            for segment in reversed(path_segments[:-1]): # 마지막 요소 제외하고 역순으로 탐색
                cleaned_segment = segment
                for suffix in common_suffixes:
                    if cleaned_segment.lower().endswith(suffix.lower()):
                        cleaned_segment = cleaned_segment[:-len(suffix)]
                
                match_id_pattern_mid = re.fullmatch(r'([a-zA-Z0-9]+(?:-[a-zA-Z0-9]+)*-\d+)', cleaned_segment, re.IGNORECASE)
                if match_id_pattern_mid:
                    filename_candidates.append(match_id_pattern_mid.group(1))
                    break # 중간에서 하나 찾으면 중단 (가장 마지막 ID 우선)


            if filename_candidates:
                # 추출된 후보 중 가장 적합해 보이는 것 선택 (예: 가장 긴 것 또는 특정 패턴 우선)
                # 여기서는 첫 번째로 찾은 유효한 후보 사용
                chosen_filename = filename_candidates[0]
                self.log_message(f"URL에서 파일명 추출 성공: '{chosen_filename}' (원본 URL: {page_url})", "DEBUG")
                return chosen_filename
            else:
                # 모든 패턴 실패 시, 마지막 경로 요소를 기본으로 사용 (단, 너무 일반적인 단어 제외)
                fallback_name = path_segments[-1]
                if len(fallback_name) <= 3 or fallback_name.lower() in ['ko','en','jp','cn','us','www']:
                    fallback_name = f"vid_from_url_{int(time.time())}"
                self.log_message(f"URL에서 특정 패턴 파일명 추출 실패, 폴백 사용: '{fallback_name}' (원본 URL: {page_url})", "WARNING")
                return fallback_name

        except Exception as e:
            self.log_message(f"URL에서 파일명 추출 중 오류: {e}", "ERROR")
            return f"url_extract_error_{int(time.time())}"


    def toggle_clipboard_monitoring(self):
        """클립보드 자동 감지 기능을 켜고 끕니다."""
        if self.auto_download_var.get(): # 체크박스가 선택되면
            if not self.folder_path_var.get(): # 저장 폴더가 선택되었는지 확인
                messagebox.showwarning("경고", "자동 다운로드를 사용하려면 먼저 저장 폴더를 선택해야 합니다.")
                self.auto_download_var.set(False) # 체크박스 선택 해제
                return
            
            self.clipboard_monitoring_active = True
            self.last_clipboard_content = "" # 마지막 클립보드 내용 초기화
            try: # 현재 클립보드 내용 가져오기 (오류 발생 가능성 있음)
                self.last_clipboard_content = self.root.clipboard_get()
            except tk.TclError: # 클립보드가 비어있거나 텍스트가 아닌 경우
                self.last_clipboard_content = "" 
            
            self.log_message("클립보드 자동 감지 시작.")
            if self.after_id_clipboard_check: # 기존에 예약된 작업이 있다면 취소
                self.root.after_cancel(self.after_id_clipboard_check)
            self.check_clipboard() # 클립보드 확인 시작
        else: # 체크박스가 해제되면
            self.clipboard_monitoring_active = False
            if self.after_id_clipboard_check: # 예약된 작업이 있다면 취소
                self.root.after_cancel(self.after_id_clipboard_check)
                self.after_id_clipboard_check = None # ID 초기화
            self.log_message("클립보드 자동 감지 중지.")
        self.update_global_ui_state() # UI 상태 업데이트


    def check_clipboard(self): # 자동 다운로드 디버깅 로그 추가됨
        """주기적으로 클립보드를 확인하여 특정 URL이 복사되면 자동 처리를 시작합니다."""
        if not self.clipboard_monitoring_active: 
            return # 감시 비활성화 시 중단

        try: 
            current_clipboard = self.root.clipboard_get()
        except tk.TclError: # 클립보드 가져오기 실패 시 (예: 내용 없음, 텍스트 아님)
            current_clipboard = ""
        
        # 클립보드 내용이 변경되었고, 비어있지 않은 경우에만 처리
        if current_clipboard and current_clipboard != self.last_clipboard_content:
            self.log_message(f"클립보드 변경 감지: '{current_clipboard[:100]}...'", "DEBUG")
            previous_clipboard_content_for_log = self.last_clipboard_content # 디버깅용 이전 내용
            self.last_clipboard_content = current_clipboard # 새 내용으로 업데이트
            
            # missav.ws 도메인을 포함하고 http/https로 시작하는지 확인
            is_target_url = "missav.ws" in current_clipboard.lower() and \
                            (current_clipboard.startswith("http://") or current_clipboard.startswith("https://"))
            self.log_message(f"MissAV URL 여부: {is_target_url} (URL: '{current_clipboard[:70]}...')", "DEBUG")

            if is_target_url:
                can_initiate_auto_processing = False
                with self.download_lock: # is_processing_auto 플래그 접근 동기화
                    self.log_message(f"자동 처리 가능성 확인 중. 현재 is_processing_auto: {self.is_processing_auto}", "DEBUG")
                    if not self.is_processing_auto: # 다른 자동 처리가 진행 중이 아닐 때만
                        self.is_processing_auto = True # 처리 시작 플래그 설정
                        can_initiate_auto_processing = True
                    else:
                         self.log_message(f"is_processing_auto가 이미 True({self.is_processing_auto})이므로 새 자동 처리 시작 안 함.", "DEBUG")
                
                if can_initiate_auto_processing:
                    self.log_message(f"자동 처리 시작 결정됨 (URL: {current_clipboard})", "INFO")
                    self.root.after(0, self.update_global_ui_state) # UI 상태 즉시 업데이트
                    # 별도 스레드에서 URL 처리 시작
                    processing_thread = threading.Thread(
                        target=self.process_copied_url, 
                        args=(current_clipboard,), 
                        name="AutoProcessURLThread"
                    )
                    processing_thread.daemon = True
                    processing_thread.start()
                else: 
                    # 이전 클립보드 내용과 현재 내용이 다를 때만 "처리 대기" 메시지 표시
                    # (같은 내용을 계속 복사하는 경우 중복 로그 방지)
                    if previous_clipboard_content_for_log != current_clipboard:
                         self.log_message(f"새 URL 감지됨 ({current_clipboard[:70]}...), 하지만 다른 자동 처리({self.is_processing_auto})가 진행 중이므로 대기합니다.", "DEBUG") # noqa
            else: # missav.ws URL이 아닌 경우
                self.log_message(f"MissAV URL 아님: '{current_clipboard[:70]}...'", "DEBUG")
        
        # 감시가 활성화되어 있다면 다음 확인 예약
        if self.clipboard_monitoring_active: 
            self.after_id_clipboard_check = self.root.after(CLIPBOARD_CHECK_INTERVAL_MS, self.check_clipboard)
    
    def process_copied_url(self, page_url): # URL 기반 파일명 사용하도록 수정
        """클립보드에서 복사된 URL을 받아 M3U8 분석 및 다운로드 큐 추가까지 처리합니다."""
        self.root.after(0, self.log_message, f"process_copied_url 스레드 시작 (URL: {page_url})", "DEBUG")
        
        # URL에서 파일명 추출
        extracted_filename_base = self.extract_filename_from_url(page_url)
        sanitized_filename_from_url = self.sanitize_filename(extracted_filename_base)

        if "error" in sanitized_filename_from_url or "fallback" in sanitized_filename_from_url or "no_path" in sanitized_filename_from_url: # noqa
            self.log_message(f"URL에서 파일명 추출에 문제 발생: '{page_url}'. 생성된 파일명: '{sanitized_filename_from_url}'", "WARNING")
        else:
            self.log_message(f"URL 기반 파일명 생성 성공: '{sanitized_filename_from_url}'", "INFO")

        # GUI 업데이트 및 M3U8 분석 시작 (메인 스레드에서 실행)
        def update_gui_and_start_analysis_callback():
            # URL 입력창 및 파일명 입력창 업데이트
            self.url_entry.delete(0,tk.END); self.url_entry.insert(0,page_url)
            self.filename_entry.delete(0,tk.END); self.filename_entry.insert(0,sanitized_filename_from_url)
            self.link_listbox.delete(0,tk.END) # 이전 링크 목록 삭제
            
            if not self.folder_path_var.get(): # 저장 폴더 확인
                messagebox.showerror("오류","저장 폴더가 선택되지 않았습니다. 자동 다운로드를 중단합니다.")
                with self.download_lock: self.is_processing_auto = False # 플래그 해제
                self.root.after(0, self.update_global_ui_state); return
            
            self.log_message(f"M3U8 분석 시작 (URL기반 파일명: '{sanitized_filename_from_url}')...")
            # M3U8 분석 스레드 시작
            analysis_thread = threading.Thread(
                target=self.analyze_m3u8_links_for_auto,
                args=(page_url,sanitized_filename_from_url), # 파일명을 로그 및 페이지 소스 저장 시 사용
                name="AutoAnalyzeM3U8Thread"
            )
            analysis_thread.daemon = True
            analysis_thread.start()
        
        self.root.after(0, update_gui_and_start_analysis_callback)


    def start_analysis_thread(self): # 수동 분석 시 URL 기반 파일명 제안 추가
        """수동 M3U8 링크 분석을 시작합니다."""
        can_start_manual_analysis = False
        with self.download_lock: # 다른 작업 진행 중인지 확인
            if not (self.active_downloads > 0 or len(self.download_queue) > 0 or 
                    self.is_processing_auto or self.is_manual_analyzing):
                self.is_manual_analyzing = True # 수동 분석 시작 플래그 설정
                can_start_manual_analysis = True
        
        if can_start_manual_analysis:
            self.root.after(0, self.update_global_ui_state) # UI 상태 업데이트 (버튼 비활성화 등)
            current_page_url = self.url_entry.get()
            if not current_page_url:
                messagebox.showerror("오류", "페이지 URL을 입력하세요.")
                with self.download_lock: self.is_manual_analyzing = False # 플래그 리셋
                self.root.after(0, self.update_global_ui_state); return
            
            # URL 기반으로 파일명 추출 및 파일명 입력창에 자동 설정
            extracted_filename = self.extract_filename_from_url(current_page_url)
            sanitized_filename_from_url = self.sanitize_filename(extracted_filename)
            self.filename_entry.delete(0, tk.END)
            self.filename_entry.insert(0, sanitized_filename_from_url)
            self.log_message(f"수동 분석: URL 기반 제안 파일명 - '{sanitized_filename_from_url}'", "INFO")
            
            self.link_listbox.delete(0, tk.END) # 이전 링크 목록 삭제
            self.log_message(f"M3U8 링크 분석 시작 (수동 - {current_page_url})...")
            # M3U8 분석 스레드 시작
            analysis_thread = threading.Thread(
                target=self.analyze_m3u8_links, 
                args=(current_page_url,), 
                name="ManualAnalyzeM3U8Thread"
            )
            analysis_thread.daemon = True
            analysis_thread.start()
        else: 
            messagebox.showwarning("대기", "다른 작업(자동 처리/분석 또는 다운로드)이 진행 중입니다.")


    def analyze_m3u8_links_for_auto(self, page_url, filename_base_for_log): # 자동 M3U8 분석
        # (V6.3.6과 거의 동일, eval 인자 추출 로직 약간 개선)
        m3u8_found_links = []; page_source = ""; driver = None; analysis_success = False
        try:
            logging.info(f"자동M3U8분석:{page_url}({filename_base_for_log})")
            self.root.after(0,self.log_message,f"페이지 로드(자동):{page_url}")
            opts=Options(); opts.add_argument("--headless"); opts.add_argument("--disable-gpu"); opts.add_argument("--no-sandbox"); opts.add_argument("--disable-dev-shm-usage"); opts.add_argument(f"user-agent={USER_AGENT}"); opts.add_experimental_option('excludeSwitches',['enable-automation']); opts.add_experimental_option('useAutomationExtension',False) # noqa
            try: 
                driver=webdriver.Chrome(service=ChromeService(ChromeDriverManager().install()),options=opts)
                driver.execute_cdp_cmd('Network.setUserAgentOverride',{"userAgent":USER_AGENT})
            except Exception as e: 
                logging.error(f"ChromeDriver로드실패(자동):{e}")
                self.root.after(0,self.log_message,f"ChromeDriver로드실패(자동):{e}.","ERROR")
                return # finally 블록에서 is_processing_auto 와 UI 상태를 처리
            
            driver.get(page_url); time.sleep(SELENIUM_JS_WAIT_TIME_S); page_source=driver.page_source
            
            try: # JS 변수에서 M3U8 추출
                js_src=driver.execute_script("return (typeof source !== 'undefined' && source.includes('.m3u8'))?source:null")
                if js_src and js_src not in m3u8_found_links:
                    m3u8_found_links.append(js_src)
                    self.root.after(0,self.log_message,f"JS 'source' M3U8(자동):{js_src[:70]}...","DEBUG")
                plyr_src=driver.execute_script("return (typeof window.player !== 'undefined' && typeof window.player.source === 'string' && window.player.source.includes('.m3u8'))?window.player.source:(typeof window.hls !== 'undefined' && typeof window.hls.url === 'string' && window.hls.url.includes('.m3u8'))?window.hls.url:null") # noqa
                if plyr_src and plyr_src not in m3u8_found_links:
                    m3u8_found_links.append(plyr_src)
                    self.root.after(0,self.log_message,f"JS Plyr/HLS M3U8(자동):{plyr_src[:70]}...","DEBUG")
            except Exception as e_js: self.root.after(0,self.log_message,f"JS 직접실행오류(자동):{e_js}","WARNING")
            
            if driver:driver.quit();driver=None # 드라이버 사용 후 즉시 종료
            
            eval_s=re.search(r"eval\s*\(\s*function\s*\(p,[^)]+\)\s*\{([\s\S]+?)\}\s*\(([^)]+)\)\s*\)",page_source,re.DOTALL)
            if eval_s:
                eval_full_call = eval_s.group(0) # eval(...) 전체 문자열
                eval_args_str = eval_s.group(2)   # eval의 인자 부분 문자열: ('packed', C, K, 'keywords'.split())
                
                # packed_code_params (첫번째 문자열 인자) 와 keywords_str (.split 앞 문자열) 추출 개선
                # 첫 번째 '...' 인자 추출 (packed_code)
                first_arg_match = re.match(r"\s*'((?:\\'|[^'])*)'", eval_args_str)
                pcode = first_arg_match.group(1) if first_arg_match else ""
                
                # .split('|') 앞의 '...' 문자열 인자 추출 (keywords_str)
                # eval_full_call 에서 찾는 것이 더 안정적일 수 있음 (인자 순서 변경 가능성 대비)
                kstr_match = re.search(r",\s*'((?:\\'|[^'])*)'\.split\('\|'\)", eval_full_call)
                kstr = kstr_match.group(1) if kstr_match else ""
                                
                if pcode and kstr:
                    deob_url=self.deobfuscate_missav_source(pcode,kstr)
                    if deob_url and deob_url not in m3u8_found_links:
                        m3u8_found_links.append(deob_url)
                        self.root.after(0,self.log_message,f"난독화해제M3U8(자동):{deob_url}","INFO")
                else:logging.warning(f"자동분석:eval인자파싱실패. Args:'{eval_args_str[:100]}...' FullEval:'{eval_full_call[:100]}...'") # noqa

            if not m3u8_found_links: # Fallback: 일반 정규식으로 M3U8 URL 검색
                for link in re.findall(r'(https?://[^\s"\'<>]+\.m3u8[^\s"\'<>]*?)',page_source,re.IGNORECASE):
                    pu=urlparse(link)
                    if pu.scheme and pu.netloc and link not in m3u8_found_links: m3u8_found_links.append(link)
                    logging.info(f"일반RegexM3U8(자동):{link}")
            
            if m3u8_found_links:
                unique_links=[li for i,li in enumerate(m3u8_found_links) if li not in m3u8_found_links[:i]]
                self.root.after(0,self._auto_download_add_to_queue,unique_links)
                analysis_success=True
            else: self.root.after(0,self.log_message,f"M3U8링크최종실패(자동-{filename_base_for_log}).","WARNING")
        except Exception as e:
            self.root.after(0,self.log_message,f"M3U8자동분석오류({filename_base_for_log}):{type(e).__name__}-{e}","ERROR")
            logging.exception(f"M3U8자동분석({filename_base_for_log})예외")
        finally: # 자동 분석 완료 후 정리 작업
            if driver:driver.quit() # 혹시 driver가 살아있으면 종료
            if page_source: # 페이지 소스가 있다면 파일로 저장 (디버깅용)
                try:
                    script_dir=os.path.dirname(os.path.abspath(__file__)if"__file__"in locals()else os.getcwd())
                    log_dir=os.path.dirname(LOG_FILENAME)if os.path.isabs(LOG_FILENAME)else script_dir
                    safe_filename="".join(c if c.isalnum()else"_"for c in filename_base_for_log)
                    source_path=os.path.join(l_dir,f"last_page_source_auto_{safe_filename[:50]}_v636.html")
                    with open(source_path,"w",encoding="utf-8")as f:f.write(page_source)
                except Exception as e_write:logging.error(f"자동페이지소스저장실패:{e_write}")
            
            # 메인 스레드에서 is_processing_auto 플래그 해제 및 UI 업데이트
            def final_callback_auto_analysis():
                with self.download_lock: 
                    self.log_message(f"자동 분석 finally 콜백: is_processing_auto 를 False로 설정. 이전 값: {self.is_processing_auto}", "DEBUG")
                    self.is_processing_auto = False
                self.update_global_ui_state()
                log_level = "DEBUG" if analysis_success else "WARNING" # 성공/실패에 따라 로그 레벨 조정
                self.log_message(f"자동 분석 완료 ({'성공'if analysis_success else'실패'}): {filename_base_for_log}",log_level)
            self.root.after(0,final_callback_auto_analysis)


    def analyze_m3u8_links(self, page_url): # 수동 M3U8 분석
        # (analyze_m3u8_links_for_auto와 유사한 로직 사용, eval 인자 추출 개선)
        m3u8_found_links=[];page_source="";driver=None
        try:
            logging.info(f"수동M3U8분석:{page_url}");self.root.after(0,self.log_message,f"페이지로드(수동):{page_url}")
            opts=Options(); opts.add_argument("--headless"); opts.add_argument("--disable-gpu"); opts.add_argument("--no-sandbox"); opts.add_argument("--disable-dev-shm-usage"); opts.add_argument(f"user-agent={USER_AGENT}"); opts.add_experimental_option('excludeSwitches',['enable-automation']); opts.add_experimental_option('useAutomationExtension',False) # noqa
            try:
                driver=webdriver.Chrome(service=ChromeService(ChromeDriverManager().install()),options=opts)
                driver.execute_cdp_cmd('Network.setUserAgentOverride',{"userAgent":USER_AGENT})
            except Exception as e:
                logging.error(f"ChromeDriver로드실패(수동):{e}"); self.root.after(0,self.log_message,f"ChromeDriver로드실패(수동):{e}.","ERROR"); return

            driver.get(page_url);time.sleep(SELENIUM_JS_WAIT_TIME_S);page_source=driver.page_source
            try: # JS 변수 추출
                js_src=driver.execute_script("return(typeof source !== 'undefined' && source.includes('.m3u8'))?source:null")
                if js_src and js_src not in m3u8_found_links:m3u8_found_links.append(js_src);self.root.after(0,self.log_message,f"JS'source'M3U8(수동):{js_src[:70]}...","DEBUG") # noqa
                plyr_src=driver.execute_script("return(typeof window.player !== 'undefined' && typeof window.player.source === 'string' && window.player.source.includes('.m3u8'))?window.player.source:(typeof window.hls !== 'undefined' && typeof window.hls.url === 'string' && window.hls.url.includes('.m3u8'))?window.hls.url:null") # noqa
                if plyr_src and plyr_src not in m3u8_found_links:m3u8_found_links.append(plyr_src);self.root.after(0,self.log_message,f"JS Plyr/HLS M3U8(수동):{plyr_src[:70]}...","DEBUG") # noqa
            except Exception as e_js:self.root.after(0,self.log_message,f"JS 직접실행오류(수동):{e_js}","WARNING")
            
            if driver:driver.quit();driver=None
            
            eval_s=re.search(r"eval\s*\(\s*function\s*\(p,[^)]+\)\s*\{([\s\S]+?)\}\s*\(([^)]+)\)\s*\)",page_source,re.DOTALL)
            if eval_s:
                eval_full_call = eval_s.group(0); eval_args_str = eval_s.group(2); pcode = ""; kstr = ""
                first_arg_match = re.match(r"\s*'((?:\\'|[^'])*)'", eval_args_str)
                if first_arg_match: pcode = first_arg_match.group(1)
                kstr_match = re.search(r",\s*'((?:\\'|[^'])*)'\.split\('\|'\)", eval_full_call)
                if kstr_match: kstr = kstr_match.group(1)
                
                if pcode and kstr: 
                    deob_url=self.deobfuscate_missav_source(pcode,kstr)
                    if deob_url and deob_url not in m3u8_found_links:
                        m3u8_found_links.append(deob_url)
                        self.root.after(0,self.log_message,f"난독화해제M3U8(수동):{deob_url}","INFO")
                else: logging.warning(f"수동분석:eval인자파싱실패.Args:'{eval_args_str[:100]}...' FullEval:'{eval_full_call[:100]}...'") # noqa

            if not m3u8_found_links: # Fallback
                for link in re.findall(r'(https?://[^\s"\'<>]+\.m3u8[^\s"\'<>]*?)',page_source,re.IGNORECASE):
                    pu=urlparse(link)
                    if pu.scheme and pu.netloc and link not in m3u8_found_links:m3u8_found_links.append(link)
                    logging.info(f"일반RegexM3U8(수동):{link}")
            
            if m3u8_found_links:
                unique_links=[li for i,li in enumerate(m3u8_found_links) if li not in m3u8_found_links[:i]]
                def update_listbox_manually_callback(): # 명확한 콜백 함수명
                    for item in unique_links:self.link_listbox.insert(tk.END,item)
                    self.log_message(f"{len(unique_links)}개 M3U8찾음(수동).")
                    if unique_links:self.link_listbox.selection_set(0) # 첫 항목 자동 선택
                self.root.after(0,update_listbox_manually_callback)
            else:self.root.after(0,self.log_message,"M3U8링크최종실패(수동).","WARNING")
        except Exception as e:
            self.root.after(0,self.log_message,f"M3U8수동분석오류:{type(e).__name__}-{e}","ERROR")
            logging.exception("M3U8수동분석예외")
        finally:
            if driver:driver.quit()
            if page_source:
                try:
                    script_dir=os.path.dirname(os.path.abspath(__file__)if"__file__"in locals()else os.getcwd())
                    log_dir=os.path.dirname(LOG_FILENAME)if os.path.isabs(LOG_FILENAME)else script_dir
                    source_path=os.path.join(l_dir,"last_page_source_manual_v636.html")
                    with open(source_path,"w",encoding="utf-8")as f:f.write(page_source)
                except Exception as e_write:logging.error(f"수동페이지소스저장실패:{e_write}")
            
            def final_callback_manual_analysis(): # 명확한 콜백 함수명
                with self.download_lock:self.is_manual_analyzing=False
                self.update_global_ui_state()
            self.root.after(0,final_callback_manual_analysis)


    def deobfuscate_missav_source(self, packed_code_params, keywords_str):
        # (V6.3.2와 동일 - 개선된 버전)
        # 우선 순위 1: packed_code_params 내에서 잘 알려진 변수명(file, source, src, f)에 할당된 M3U8 URL 직접 찾기
        direct_m3u8_regex=r"""(?:file|source|src|f)\s*[:=]\s*(["'])(https?://(?:[a-zA-Z0-9.\-_]+|\[[a-fA-F0-9:]+\])(?:[:\d]+)?(?:/(?:[\w.,@?^=%&:/~+#-]*[\w@?^=%&/~+#-])?)?\.m3u8(?:[\?&][\w.,@?^=%&:/~+#-=]*)?)\1""" # noqa
        match_direct=re.search(direct_m3u8_regex,packed_code_params,re.VERBOSE|re.IGNORECASE)
        if match_direct:extracted_url=match_direct.group(2);logging.info(f"난독화해제(직접M3U8v2):{extracted_url}");return extracted_url
        
        # 우선 순위 2: packed_code_params 내에서 좀 더 일반적인 M3U8 URL 패턴 찾기
        simple_url_match=re.search(r"(https?://[^\s\"'<>]+\.m3u8[^\s\"'<>]*)",packed_code_params)
        if simple_url_match:extracted_url=simple_url_match.group(1);logging.info(f"난독화해제(간단M3U8):{extracted_url}");return extracted_url
        
        # 우선 순위 3: 키워드 기반 재구성 (최후의 수단, 사이트 변경에 취약)
        logging.warning("직접M3U8 URL못찾음.키워드기반재구성시도(취약).")
        keywords=keywords_str.split('|')
        # 현재는 하나의 패턴만 정의. 필요시 여기에 다른 패턴(idx_map_pattern2 등) 추가 가능
        idx_map_pattern1={'name':"기본패턴(seg5-seg4-..)",'protocol_idx':8,'domain1_idx':7,'domain2_idx':6,'path_indices':[5,4,3,2,1],'path_separator':'-','filename_idx':14,'extension_idx':0} # noqa
        patterns_to_try=[idx_map_pattern1]
        
        for pattern_num,current_pattern in enumerate(patterns_to_try):
            pattern_name=current_pattern.get('name',f"패턴{pattern_num+1}")
            try:
                # 필요한 모든 인덱스가 키워드 배열 범위 내에 있는지 확인
                required_indices = [current_pattern['protocol_idx'], current_pattern['domain1_idx'], 
                                    current_pattern['domain2_idx'], current_pattern['filename_idx'], 
                                    current_pattern['extension_idx']] + current_pattern['path_indices']
                if any(idx>=len(keywords)for idx in required_indices):
                    logging.debug(f"키워드재구성 {pattern_name}:인덱스범위초과. 건너뜁니다.");continue
                
                protocol=keywords[current_pattern['protocol_idx']]
                domain=f"{keywords[current_pattern['domain1_idx']]}.{keywords[current_pattern['domain2_idx']]}"
                path_segments=[keywords[i]for i in current_pattern['path_indices']]
                path_str=current_pattern['path_separator'].join(path_segments)
                filename=keywords[current_pattern['filename_idx']]
                extension=keywords[current_pattern['extension_idx']]

                # 기본적인 유효성 검사
                if not(protocol.startswith("http")and"."in domain and filename and extension):
                    logging.debug(f"키워드재구성 {pattern_name}:필수요소누락/형식오류. 건너뜁니다.");continue
                    
                constructed_url=f"{protocol}://{domain}/{path_str}/{filename}.{extension}"
                
                if constructed_url.endswith(".m3u8"): # .m3u8로 끝나는지 확인
                    logging.info(f"난독화해제({pattern_name} 사용):{constructed_url}");return constructed_url
                else: # .m3u8로 끝나지 않을 경우, 강제 .m3u8 시도
                    if extension.lower()!="m3u8"and"m3u8"in(k.lower()for k in keywords): # 키워드 중에 'm3u8'이 있다면
                        constructed_url_forced_m3u8=f"{protocol}://{domain}/{path_str}/{filename}.m3u8"
                        if constructed_url_forced_m3u8.endswith(".m3u8"): # 다시 확인
                            logging.info(f"난독화해제({pattern_name},강제.m3u8확장자):{constructed_url_forced_m3u8}");return constructed_url_forced_m3u8 # noqa
                    logging.debug(f"키워드재구성 {pattern_name}:생성된URL이.m3u8로 끝나지 않음 - {constructed_url}")
            except IndexError:logging.warning(f"키워드재구성 {pattern_name} 처리 중 인덱스 오류 발생.")
            except Exception as e:logging.error(f"키워드재구성 {pattern_name} 처리 중 예외: {e}")
            
        logging.error(f"모든 방법으로 M3U8 URL 난독화 해제 실패. Packed:{packed_code_params[:150]}, Keywords:{keywords_str[:100]}");return None # noqa

    def _auto_download_add_to_queue(self, found_m3u8_links):
        # (V6.3.6과 동일)
        if not found_m3u8_links:self.log_message("자동다운로드큐추가실패:M3U8링크없음.","WARNING");return
        out_fname=self.filename_entry.get().strip();ref_url=self.url_entry.get();dl_folder=self.folder_path_var.get()
        if not out_fname:out_fname=f"auto_video_{int(time.time())}"
        if not dl_folder:self.log_message("자동다운로드폴더오류!","ERROR");messagebox.showerror("치명적오류","저장폴더설정안됨.");return
        final_path=os.path.join(dl_folder,f"{out_fname}.mp4");m3u8_dl_url=found_m3u8_links[0]
        self.link_listbox.delete(0,tk.END);[self.link_listbox.insert(tk.END,link)for link in found_m3u8_links];
        if found_m3u8_links:self.link_listbox.selection_set(0)
        self.log_message(f"자동다운로드:'{out_fname}'M3U8'{m3u8_dl_url[:50]}...'확인.")
        with self.download_lock:self.download_queue.append((m3u8_dl_url,final_path,ref_url,out_fname));self.log_message(f"'{out_fname}'자동다운로드큐추가(대기:{len(self.download_queue)}).","INFO") # noqa
        self.try_start_next_download()

    def start_manual_download(self):
        # (V6.3.6과 동일)
        sel_idx=self.link_listbox.curselection()
        if not sel_idx:messagebox.showwarning("경고","다운로드할M3U8링크선택.");return
        m3u8_url=self.link_listbox.get(sel_idx[0]);dl_folder=self.folder_path_var.get()
        if not dl_folder:messagebox.showerror("오류","저장폴더선택.");return
        out_fname=self.filename_entry.get().strip()
        if not out_fname: # 파일명 입력창이 비었으면 URL 기반으로 다시 생성
            current_page_url = self.url_entry.get()
            if current_page_url: extracted_filename = self.extract_filename_from_url(current_page_url); out_fname = self.sanitize_filename(extracted_filename) # noqa
            else: path_part=urlparse(m3u8_url).path.split('/')[-1];def_name=path_part.replace('.m3u8','')if path_part else"video";out_fname=f"downloaded_{def_name}" # noqa
            self.filename_entry.delete(0,tk.END);self.filename_entry.insert(0,out_fname)
        final_path=os.path.join(dl_folder,f"{out_fname}.mp4");ref_url=self.url_entry.get()
        if not os.path.exists(dl_folder):
            try:os.makedirs(dl_folder)
            except Exception as e:self.log_message(f"다운로드폴더생성실패:{e}","ERROR");return
        with self.download_lock:self.download_queue.append((m3u8_url,final_path,ref_url,out_fname));self.log_message(f"'{out_fname}'다운로드큐추가(대기:{len(self.download_queue)}).","INFO") # noqa
        self.try_start_next_download();self.root.after(0,self.update_global_ui_state)

    def try_start_next_download(self):
        # (V6.3.6과 동일)
        with self.download_lock:
            if self.active_downloads<self.MAX_CONCURRENT_DOWNLOADS and self.download_queue:
                m3u8_url,final_target_path,ref_url,disp_fname=self.download_queue.popleft();slot_idx=-1
                for i in range(self.MAX_CONCURRENT_DOWNLOADS):
                    if self.progress_elements[i]['active_file_key']is None:
                        slot_idx=i;self.progress_elements[i]['active_file_key']=final_target_path
                        self.progress_elements[i]['_filename_for_display']=disp_fname
                        self.progress_elements[i]['label'].config(text=f"{disp_fname[:30]}... (준비 중)")
                        self.progress_elements[i]['bar']['value']=0;break
                if slot_idx==-1:
                    self.log_message("오류:사용가능슬롯없음(이론상불가)","ERROR")
                    self.download_queue.appendleft((m3u8_url,final_target_path,ref_url,disp_fname));return
                self.active_downloads+=1
                self.log_message(f"'{disp_fname}'다운로드시작(슬롯{slot_idx+1})...(활성:{self.active_downloads},대기:{len(self.download_queue)})","INFO") # noqa
                self.root.after(0,self.update_global_ui_state)
                main_dl_folder=os.path.dirname(final_target_path);base_fname_ext=os.path.basename(final_target_path)
                tmp_dir_path=os.path.join(main_dl_folder,TEMP_DOWNLOAD_SUBDIR);os.makedirs(tmp_dir_path,exist_ok=True)
                actual_dl_path=os.path.join(tmp_dir_path,base_fname_ext)
                threading.Thread(target=self.download_with_yt_dlp,args=(m3u8_url,actual_dl_path,final_target_path,ref_url,disp_fname,slot_idx),name=f"Downloader-{disp_fname[:20]}").start() # noqa
            elif not self.download_queue and self.active_downloads==0:self.root.after(0,self.update_global_ui_state)


    def download_with_yt_dlp(self,m3u8_url,actual_dl_path,final_target_path,ref_url,disp_fname,slot_idx):
        # (V6.3.6과 동일)
        ret_code=-1;success_dl=False
        try:
            cmd=['yt-dlp','--force-overwrites','--no-part','--referer',ref_url,'-o',actual_dl_path,m3u8_url]
            logging.info(f"yt-dlp실행({disp_fname},슬롯{slot_idx}):{' '.join(cmd)}")
            c_flags=subprocess.CREATE_NO_WINDOW if os.name=='nt'else 0
            proc=subprocess.Popen(cmd,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,encoding='utf-8',errors='replace',creationflags=c_flags) # noqa
            for line in iter(proc.stdout.readline,""):
                match_prog=re.search(r"\[download\]\s+([\d.]+)%\s+of\s+(?:~\s*)?([\d.]+\s*[KMGTiBps]+)",line)
                if match_prog:
                    perc_str,size_str=match_prog.groups()
                    try: perc_float=float(perc_str); self.root.after(0,self.update_ui_specific_progress,slot_idx,perc_float,disp_fname,size_str.strip()) # noqa
                    except ValueError: logging.warning(f"진행률 파싱 오류: {perc_str}")
                else:
                    trim_line=line.strip()
                    if trim_line and not trim_line.startswith(("[debug]","[info] Merging"))and"ETA"not in trim_line and"Defaulting to HLS Native"not in trim_line and"Extracting URL"not in trim_line:self.root.after(0,self.log_message,f"{trim_line}","DEBUG_YT") # noqa
                logging.debug(f"YT-DLP STDOUT({disp_fname}):{line.strip()}")
            proc.stdout.close();stderr_out=proc.stderr.read();ret_code=proc.wait()
            if ret_code==0:
                success_dl=True;self.log_message(f"임시다운로드성공:'{disp_fname}'->{actual_dl_path}","INFO")
                try:
                    final_dir=os.path.dirname(final_target_path);os.makedirs(final_dir,exist_ok=True)
                    final_move_path=final_target_path;ctr=1;name_p,ext_p=os.path.splitext(final_target_path)
                    while os.path.exists(final_move_path):final_move_path=f"{name_p}({ctr}){ext_p}";ctr+=1
                    shutil.move(actual_dl_path,final_move_path);self.log_message(f"파일이동성공:'{disp_fname}'->{final_move_path}","INFO");logging.info(f"yt-dlp성공및이동:{m3u8_url}->{final_move_path}") # noqa
                except Exception as e_mv:success_dl=False;self.log_message(f"오류:'{disp_fname}'파일이동실패.임시:{actual_dl_path}.오류:{e_mv}","ERROR");logging.error(f"파일이동실패({disp_fname}):{e_mv}.임시:{actual_dl_path}") # noqa
            else:
                self.log_message(f"오류:'{disp_fname}'yt-dlp다운로드실패(코드:{ret_code}).","ERROR")
                if stderr_out:self.log_message(f"[yt-dlp ERROR]{stderr_out.strip()}","ERROR")
                logging.error(f"yt-dlp실패({disp_fname},코드{ret_code})M3U8:{m3u8_url}\nStderr:{stderr_out}")
                if os.path.exists(actual_dl_path):
                    try:os.remove(actual_dl_path);logging.info(f"실패임시파일삭제:{actual_dl_path}")
                    except OSError as e_del:logging.warning(f"실패임시파일삭제오류{actual_dl_path}:{e_del}")
        except FileNotFoundError:self.log_message("yt-dlp/FFmpeg설치확인및PATH설정필요.","ERROR");logging.error("yt-dlp/FFmpeg FileNotFoundError") # noqa
        except Exception as e:self.log_message(f"다운로드중오류({disp_fname}):{type(e).__name__}","ERROR");logging.exception(f"다운로드({disp_fname})예외") # noqa
        finally:
            with self.download_lock:self.active_downloads-=1
            def final_actions_after_download_completed_v3(): # 명확한 콜백 함수명
                self.clear_progress_slot(slot_idx,"완료"if success_dl else"실패",success_dl)
                self.log_message(f"'{disp_fname}'다운로드작업종료(활성:{self.active_downloads},대기:{len(self.download_queue)})","DEBUG") # noqa
                self.try_start_next_download();self.update_global_ui_state()
            self.root.after(0,final_actions_after_download_completed_v3)

if __name__ == "__main__":
    root = tk.Tk()
    app = VideoDownloaderApp(root)
    root.mainloop()
