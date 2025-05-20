#coding:utf8
#title_en: 123av Downloader (Hitomi Downloader Plugin)
#comment:https://123av.com/
from utils import Downloader,try_n,LazyUrl,get_print,Soup,clean_title
from error_printer import print_error
from m3u8_tools import M3u8_stream
from downloader import read_html as get
from io import BytesIO
import urllib.parse # urljoin을 위해 필요

class Video:
    def __init__(self,url,cwz):
        self.cw=cwz
        extraction_result = self.getx(url) # getx는 (최종 m3u8 URL, 중간 URL의 origin)을 반환
        if extraction_result is None:
            get_print(cwz)("Failed to extract video data from getx.")
            raise ValueError("getx did not return video data.")

        # getx로부터 받은 URL은 마스터 플레이리스트 URL일 가능성이 높음
        master_playlist_url, intermediate_url_origin = extraction_result

        get_print(cwz)(f"Extracted Master Playlist URL (tentative): {master_playlist_url}")
        if intermediate_url_origin:
            get_print(cwz)(f"Potential Referer Origin: {intermediate_url_origin}")

        actual_media_playlist_url_to_use = None
        m3u8_content = None

        # 1. 마스터 플레이리스트 내용 가져오기
        try:
            get_print(cwz)(f"Fetching content of: {master_playlist_url}")
            fetch_headers = {}
            if intermediate_url_origin: # 이전 단계에서 얻은 intermediate URL의 origin을 Referer로 사용
                fetch_headers['Referer'] = intermediate_url_origin
            # User-Agent는 Hitomi Downloader의 'get' 함수가 내부적으로 적절히 설정할 것으로 기대
            
            # get 함수가 bytes를 반환할 경우를 대비해 decode 추가
            raw_content = get(master_playlist_url, headers=fetch_headers) if fetch_headers else get(master_playlist_url)
            if isinstance(raw_content, bytes):
                m3u8_content = raw_content.decode('utf-8', errors='replace')
            else:
                m3u8_content = str(raw_content)

            get_print(cwz)("--- Master Playlist Content Start ---")
            get_print(cwz)(m3u8_content[:1000]) # 내용 일부 출력
            get_print(cwz)("--- Master Playlist Content End ---")

            if not m3u8_content or "#EXTM3U" not in m3u8_content:
                 get_print(cwz)("WARNING: Fetched content seems empty or not a valid M3U8 playlist.")
                 raise ValueError("Invalid or empty M3U8 master playlist content.")

        except Exception as e_fetch:
            get_print(cwz)(f"Failed to fetch or decode master playlist content: {print_error(e_fetch)}")
            raise # 이 단계 실패 시 중단

        # 2. 마스터 플레이리스트 파싱하여 미디어 플레이리스트 URL 선택
        if "#EXT-X-STREAM-INF:" in m3u8_content: # 마스터 플레이리스트인지 확인
            get_print(cwz)("Master playlist detected. Parsing for variant streams...")
            lines = m3u8_content.strip().split('\n')
            variant_urls = []
            best_bandwidth = 0
            chosen_variant_relative_url = None

            for i, line in enumerate(lines):
                if line.startswith("#EXT-X-STREAM-INF:"):
                    try:
                        # BANDWIDTH 추출 시도 (가장 높은 대역폭 선택용)
                        bandwidth_str = None
                        if "BANDWIDTH=" in line:
                            bandwidth_str = line.split("BANDWIDTH=")[1].split(",")[0]
                        current_bandwidth = int(bandwidth_str) if bandwidth_str and bandwidth_str.isdigit() else 0
                        
                        if i + 1 < len(lines) and not lines[i+1].startswith("#"): # 다음 라인이 URL이라고 가정
                            relative_url = lines[i+1].strip()
                            variant_urls.append({'url': relative_url, 'bandwidth': current_bandwidth, 'info': line})
                            if current_bandwidth >= best_bandwidth: # 등호 포함 (동일 대역폭이면 나중 것 선택)
                                best_bandwidth = current_bandwidth
                                chosen_variant_relative_url = relative_url
                    except Exception as e_parse_inf:
                        get_print(cwz)(f"Error parsing #EXT-X-STREAM-INF line: {line} - {e_parse_inf}")
            
            if not chosen_variant_relative_url and variant_urls: # 최고 대역폭 못 찾으면 첫번째 것 선택
                chosen_variant_relative_url = variant_urls[0]['url']
                get_print(cwz)(f"Could not determine best bandwidth, selected first variant: {chosen_variant_relative_url}")

            if chosen_variant_relative_url:
                # urllib.parse.urljoin을 사용하여 절대 URL 생성
                actual_media_playlist_url_to_use = urllib.parse.urljoin(master_playlist_url, chosen_variant_relative_url)
                get_print(cwz)(f"Selected Media Playlist URL (highest bandwidth or first): {actual_media_playlist_url_to_use}")
            else:
                get_print(cwz)("No variant streams found in master playlist.")
                raise ValueError("No variant streams found in master playlist.")
        else:
            # 마스터 플레이리스트가 아니라면, 받은 URL을 그대로 사용 (이미 미디어 플레이리스트일 경우)
            get_print(cwz)("Content does not appear to be a master playlist. Using provided URL as media playlist.")
            actual_media_playlist_url_to_use = master_playlist_url


        # 3. 선택된 미디어 플레이리스트 URL로 M3u8_stream 객체 생성
        stream_obj = None
        last_exception = None
        if actual_media_playlist_url_to_use:
            get_print(cwz)(f"Attempting M3u8_stream with Media Playlist URL: {actual_media_playlist_url_to_use}")
            try:
                # 리퍼러는 intermediate_url_origin (예: 5masterzzz.site) 사용 시도
                # User-Agent 등 다른 헤더는 M3u8_stream이 Hitomi Downloader 전역 설정을 따를 것으로 기대
                m3u8_referer = intermediate_url_origin if intermediate_url_origin else None

                stream_obj = M3u8_stream(
                    actual_media_playlist_url_to_use,
                    referer=m3u8_referer, # Referer 설정
                    deco=self.cbyte,
                    n_thread=4
                )
                get_print(cwz)(f"Successfully initialized M3u8_stream with: {actual_media_playlist_url_to_use}")

            except Exception as e:
                last_exception = e
                get_print(cwz)(f"M3u8_stream initialization failed for media playlist: {print_error(e)}")
        else:
            get_print(cwz)("No valid media playlist URL to process.")
            # 이 경우는 위에서 이미 raise 되었어야 함

        if stream_obj is None:
            get_print(cwz)("M3u8_stream could not be initialized.")
            if last_exception:
                raise last_exception
            else: # 실제로는 이 분기로 오기 어려움
                raise ValueError("M3u8_stream could not be initialized (no media playlist URL).")

        if hasattr(stream_obj, 'live') and stream_obj.live is not None:
             get_print(cwz)("Stream has 'live' attribute. (Handling may be needed)")

        self.th = BytesIO()
        if hasattr(self, 'uth') and self.uth:
            try:
                download(self.uth, buffer=self.th)
            except Exception as e:
                get_print(cwz)(f"Thumbnail download error: {print_error(e)}")
        else:
            get_print(cwz)("Thumbnail URL (self.uth) not found.")

        self.url = LazyUrl(url, lambda _: stream_obj, self)

    def cbyte(self,dato):
        return dato[8:]

    @try_n(2)
    def getx(self,url):
        # (getx 메소드 내용은 이전과 거의 동일하게 유지)
        print_ = get_print(self.cw)
        print_(f"Fetching main page: {url}")
        try:
            soup = Soup(get(url))
        except Exception as e:
            print_(f"Error fetching main page: {print_error(e)}")
            raise

        player_tag = soup.find(id='player')
        if player_tag and 'data-poster' in player_tag.attrs:
            self.uth = player_tag['data-poster']
            print_(f"Found thumbnail URL: {self.uth}")
        else:
            self.uth = None
            print_("Thumbnail URL not found in player tag.")

        title_tag = soup.find('h1')
        if title_tag and title_tag.text:
            self.filename = clean_title(title_tag.text.strip()) + '.mp4'
            if len(self.filename) > 209:
                self.filename = self.filename[:205] + '.mp4'
            print_(f"Determined filename: {self.filename}")
        else:
            self.filename = "default_video.mp4"
            print_("Title tag not found, using default filename.")

        page_video_tag = soup.find(id='page-video')
        if not (page_video_tag and 'v-scope' in page_video_tag.attrs):
            print_("Could not find 'page-video' tag or 'v-scope' attribute.")
            raise ValueError("Essential video metadata for AJAX call not found on page.")
        
        idv_scope_text = page_video_tag['v-scope']
        
        id_marker = 'id: '
        id_start_index = idv_scope_text.find(id_marker)
        if id_start_index == -1:
            print_(f"'{id_marker}' not found in v-scope: {idv_scope_text}")
            raise ValueError("Video ID marker not found in v-scope.")
        
        id_start_index += len(id_marker)
        id_end_index = idv_scope_text.find(',', id_start_index)
        if id_end_index == -1:
            id_end_index = len(idv_scope_text)
            
        video_id_from_scope = idv_scope_text[id_start_index:id_end_index].strip()
        if not video_id_from_scope:
            print_("Extracted video ID is empty.")
            raise ValueError("Failed to extract a valid video ID.")
        print_(f"Extracted video ID from scope: {video_id_from_scope}")

        ajax_url_1 = f'https://123av.com/en/ajax/v/{video_id_from_scope}/videos'
        print_(f"Constructed AJAX URL 1: {ajax_url_1}")

        try:
            ajax_response_1_text = get(ajax_url_1)
        except Exception as e:
            print_(f"Error fetching AJAX URL 1: {print_error(e)}")
            raise

        url_key_pattern = '"url":"'
        intermediate_url_start_index = ajax_response_1_text.find(url_key_pattern)
        if intermediate_url_start_index == -1:
            print_(f"Pattern '{url_key_pattern}' not found in AJAX response 1: {ajax_response_1_text[:500]}...")
            raise ValueError("Could not find intermediate URL pattern in AJAX response 1.")
        
        intermediate_url_start_index += len(url_key_pattern)
        intermediate_url_end_index = ajax_response_1_text.find('"', intermediate_url_start_index)
        if intermediate_url_end_index == -1:
            print_(f"Could not find closing quote for intermediate URL: {ajax_response_1_text[intermediate_url_start_index-len(url_key_pattern):500]}...")
            raise ValueError("Malformed intermediate URL in AJAX response 1.")
            
        intermediate_url = ajax_response_1_text[intermediate_url_start_index:intermediate_url_end_index].replace('\\/', '/')
        print_(f"Extracted intermediate URL: {intermediate_url}")

        intermediate_url_origin = None
        if intermediate_url:
            try:
                parsed_uri = urllib.parse.urlparse(intermediate_url)
                intermediate_url_origin = f"{parsed_uri.scheme}://{parsed_uri.netloc}/"
            except Exception as e:
                print_(f"Could not parse intermediate_url to get origin: {e}")

        try:
            soup_intermediate = Soup(get(intermediate_url))
        except Exception as e:
            print_(f"Error fetching intermediate URL page: {print_error(e)}")
            raise

        player_tag_intermediate = soup_intermediate.find(id='player')
        if not (player_tag_intermediate and 'v-scope' in player_tag_intermediate.attrs):
            print_("Could not find 'player' tag or 'v-scope' on intermediate page.")
            raise ValueError("Essential video metadata not found on intermediate page.")

        player_v_scope_text_intermediate = player_tag_intermediate['v-scope']
        
        temp_html_for_parsing = Soup(f'<p>{player_v_scope_text_intermediate}</p>')
        final_scope_string = temp_html_for_parsing.string
        
        if final_scope_string is None:
            print_("Failed to extract string content from intermediate page's v-scope.")
            raise ValueError("Could not get string from final v-scope.")

        https_pos = final_scope_string.find('https')
        if https_pos == -1:
            print_(f"'https' not found in final scope string: {final_scope_string[:500]}...")
            raise ValueError("Final m3u8 URL (starting with https) not found.")
            
        final_m3u8_url_end_pos = final_scope_string.find('"', https_pos)
        if final_m3u8_url_end_pos == -1:
            temp_end_options = [final_scope_string.find("'", https_pos),
                                final_scope_string.find(' ', https_pos),
                                final_scope_string.find(',', https_pos)]
            temp_end_options = [opt for opt in temp_end_options if opt != -1]
            if temp_end_options:
                final_m3u8_url_end_pos = min(temp_end_options)
            else:
                final_m3u8_url_end_pos = len(final_scope_string)
            if final_m3u8_url_end_pos <= https_pos:
                 print_(f"Valid end for final m3u8 URL not found: {final_scope_string[https_pos:https_pos+100]}...")
                 raise ValueError("Malformed final m3u8 URL string (end not found).")

        # getx가 반환하는 것은 마스터 플레이리스트 URL일 가능성이 높음
        master_playlist_url_extracted = final_scope_string[https_pos:final_m3u8_url_end_pos].replace('\\/', '/')
        print_(f"Extracted Master Playlist URL (from getx): {master_playlist_url_extracted}")
        
        return master_playlist_url_extracted, intermediate_url_origin


class Downloader_123av(Downloader):
    type = '123av'
    single = True
    strip_header = False
    URLS = ['123av.com']
    display_name = '123av'
    MAX_PARALLEL = 2
    icon = 'base64:iVBORw0KGgoAAAANSUhEUgAAACAAAAAgCAYAAABzenr0AAAAAXNSR0IArs4c6QAABPBJREFUWEeVV11sFFUUPudOK9SICv3Z2W0gARuwRvEnUaSwU41P+GJITI2+QeTBF2I7s9vYGAMxRqTTgokmjYT44oNgfDHxxcQwO1uIEhP/kJQgBFNiZ6U2Gtq0AjPH3PnZuTN7Z7vsw3Z759xvv/t955x7FmGVFwIANYkJnvN3/moWmQSJcDHxBfVVAKQAzhkY76E23IOAowDULyMUb8OPiXlTqlW6IOOctRcWBo5vutXuVgHoqloxnuebb+z6YJ2rKNMAsD0Gyz5pCnweEE+oFX0sVkiujo84VzRnEGEb/+wx2Kt4mCOgKV9Zvg9jRXiMv8y1IwloksmSh96eQqVc9Q8hkcAnUNNMovjh7wDQh4BALbifBBa+QSAPBKdV23hFjI3wfQKOZiaOkpl40oRpdDsj7JxqG7uiaJFfoEBGpsdgCITe3wS0l93GSzcfWPNv3/KCu7C84d7/Om4VFBf2EUI5DRPYVX87otrGW0GMUD9pBTK8upCrGo/Frsh1qmnm2wT4bqI4hVDFdXu7z43+GeFILZDK5NGzuenS9z73jPyL9t0oHst76F4kgAfjpK23ia9U23gpWm8kQHAAEE6IMvHPLuBTvbb+Y309ahQZ1vnVpY3PI2BnWqt81cDI85hAGMUYFF3w+tBjnwrYS8tssWuLdWglnSuZCcsPvHOyw2nzriODDYmCWkv3qd+UliIVwBk0/YrzwRTcqZ7Rv3MGJ18Dovd5LboIxV5bn5V3suxy5fGzz3zU2bZ2ZV5sZuS5Q4Wz5S+4CgkL+D8KsR1d1ZHzye63So8PmUW5Uf8b2u4UJz4BpAMRJgFM5W3jjVgBoQ94jD1dsEZ+kFnbTO504or7ay8cz9GdO05kAxFM56tGscECvsCY92SPVf5JWs+ZCSd2i6h7J61xtIk/AGhTGPmLapce90uVL8wlOiHbrtojvzZJ7nSBBG0l82oIiDiaeRYABkLcy6ptbJVboLBHC2dGfhOaVYpL3MGSmZE0KG2Xo018DUAvhuyvqba+uU5AbMVtDPq7LGNmVQUyPEqYIfSKmmaeIoAhKQGxFd8Gb+tGu3w5qI8mLY9f0TzLpQUSlUVQBvzmc7TxkICPeUWtGn3Bk/Rt6CkP5aeHr7Y0XGVcezL1nEHzFBAM8S0ewEzeNvqlOUBM2Zy3hq+1kPAtuBQznNPMk4iwPxxwflYrxhNSAu3M3dhpjV5vAV0cmOIrN+OO8AkA7A9xz6u2sUNKgDEv32OVHZFA4JRkPqofMJjZmt1PSQJYUW39uYAAAjjFiXAGBsD2NjX37Zu1xplAUmapWTGhWio/HO3ohwB4MBwwP1crpVfrM4NYBcSgO28Z8/KL524m/6SJnAAiO+hfQB5O5qZ1XWrBGqasX28N/9PQiGSnvZsq0MZDBXzZSzlbN6UEFNe9v/vs6E1pEvpf2GgFj5XOCUJobfDoESI2GuDS66pdOtlIAAEYLq7rsQ4vti52sun4oJL+5RTN9wBxzPed8OVcVf9S2opzrLMDrX0rzcswOlorF3SAFBCAMf6ZIdN6KiP+j5VkJ0SgXEVXELFec+IPFD843JU5x6eZhxxrRfMwIbzDH4t55mPO7TZnkME2BJjL2UYhmYCtn1Lg15ATtd3HthBzrxDBpUK19HB0Qp8APXL6nr+6Zj9ziQ4VqqWLmf5ncGlclgRm7P0fnCtBQfdRM4sAAAAASUVORK5CYII='
    ACCEPT_COOKIES = [r'(.*\.)?(123av)\.(com)']

    @try_n(2)
    def read(self):
        video = Video(self.url, self.cw)
        self.urls.append(video.url)
        self.title = video.filename
        if hasattr(video, 'th') and video.th and video.th.getbuffer().nbytes > 0:
             self.setIcon(video.th)
        else:
             self.print_("No thumbnail data to set icon.")
