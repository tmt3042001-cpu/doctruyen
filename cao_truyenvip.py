import time
import random
import os
import re
import cloudscraper
from bs4 import BeautifulSoup

# === CẤU HÌNH CƠ BẢN ===
START_URL = "https://truyenmoiss.org/con-duong-ba-chu-534267/chuong-3170"
BASE_URL = "https://truyenmoiss.org"
PREFIX_NAME = "Con_Duong_Ba_Chu"
LOG_FILE = "save_progress.txt"
START_CHAPTER_NUM = 1740
MAX_RETRIES_PER_CHAPTER = 3  # Thử lại tối đa 3 lần cho cùng 1 URL trước khi nhảy chương

# Khởi tạo Cloudscraper với Header tối ưu
scraper = cloudscraper.create_scraper(
    browser={
        'browser': 'chrome',
        'platform': 'windows',
        'desktop': True
    }
)
scraper.headers.update({
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36',
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
    'Accept-Language': 'vi-VN,vi;q=0.9,en-US;q=0.8',
    'Referer': BASE_URL,
})

def get_chapter_num_from_url(url, default_num):
    """Trích xuất số chương thực tế từ URL để tránh lệch dữ liệu"""
    match = re.search(r'chuong-(\d+)', url, re.IGNORECASE)
    return int(match.group(1)) if match else default_num

def dedupe_consecutive_lines(lines):
    """
    NGUYÊN NHÂN GÂY TRÙNG LẶP: một số chương trên trang nguồn chèn sẵn mỗi đoạn văn
    HAI LẦN liên tiếp trong HTML (kỹ thuật chống copy/crawl phổ biến - bản thứ hai
    thường bị ẩn đi bằng CSS nên người đọc bình thường không thấy, nhưng
    BeautifulSoup vẫn đọc được cả hai vì nó không xử lý CSS).
    Hàm này loại bỏ dòng bị lặp lại NGAY LIỀN KỀ dòng trước đó.
    """
    deduped = []
    for line in lines:
        if deduped and deduped[-1] == line:
            continue
        deduped.append(line)
    return deduped

def clean_junk_text(lines):
    """
    Lọc bỏ các thông tin rác cuối chương: Lời chúc, Donate, Ngân hàng, Momo, Paypal, Email,...
    """
    cleaned_lines = []
    
    # Dấu hiệu BẮT ĐẦU khối donate/lời nhắn cuối chương (chạm vào là CẮT BỎ toàn bộ đoạn sau)
    junk_start_keywords = [
        "ai có lòng ủng hộ", "thông tin ủng hộ", "mọi sự ủng hộ", 
        "chúc cả nhà", "chúc mọi người", "chân thành cảm ơn",
        "dịch giả:", "converter:", "tác giả có lời muốn nói",
        "e chân thành cảm ơn", "em chân thành cảm ơn"
    ]
    
    # Từ khóa lọc các dòng đơn lẻ chứa thông tin ngân hàng/ví điện tử
    junk_line_keywords = [
        "số tk:", "số tk", "momo", "viettelpay", "zalopay", "paypal",
        "agribank", "vietcombank", "techcombank", "mbbank", "nguyen phuoc hau",
        "[email protected]", "ngủ ngon"
    ]

    for line in lines:
        line_str = line.strip()
        line_lower = line_str.lower()
        
        if not line_str:
            continue

        # 1. Bỏ qua các ký tự phân cách rác: ///, ---, ***, ===
        if re.match(r'^[/\-\*\=_]{2,}$', line_str):
            continue
            
        # 2. Cắt bỏ ngay lập tức nếu gặp câu mở đầu đoạn Donate/Lời chúc
        if any(keyword in line_lower for keyword in junk_start_keywords):
            break
            
        # 3. Lọc bỏ các dòng chứa thông tin tài khoản/ví điện tử lẻ tẻ
        if any(keyword in line_lower for keyword in junk_line_keywords):
            continue
            
        cleaned_lines.append(line_str)
        
    return cleaned_lines

def parse_chapter(url):
    clean_title = ""
    content = ""
    next_url = None
    
    try:
        response = scraper.get(url, timeout=20)
        
        # Lỗi 404 = Đã đến chương cuối cùng của truyện
        if response.status_code == 404:
            print("[-] Thông báo: Trang trả về 404 (Đã chạm tới chương cuối cùng).")
            return None, None, None
            
        if response.status_code != 200:
            print(f"[-] Lỗi HTTP Status: {response.status_code}")
            return clean_title, content, next_url
            
        soup = BeautifulSoup(response.content, 'html.parser')
        
        # Kiểm tra bị Cloudflare chặn
        page_title = soup.title.text.strip() if soup.title else ""
        if any(cf in page_title.lower() for cf in ["cloudflare", "just a moment", "attention required"]):
            print(f"[!] BỊ CHẶN: Phát hiện trang xác thực Cloudflare.")
            return clean_title, content, next_url

        # 1. TRÍCH XUẤT TIÊU ĐỀ
        title_tag = (
            soup.find('a', class_='chapter-title') or 
            soup.find('h2', class_='chapter-title') or 
            soup.find('h1') or 
            soup.find('span', class_='chapter-text')
        )
        if title_tag:
            clean_title = re.sub(r'\s+', ' ', title_tag.text.strip())

        # 2. BÓC TÁCH NỘI DUNG
        main_content = (
            soup.find('div', class_='chapter-c') or 
            soup.find('div', class_='chapter-content') or 
            soup.find('div', id='chapter-c') or
            soup.find('div', id='chapter-content') or
            soup.find('div', class_='reading-detail')
        )
        
        # Thuật toán dự phòng: Tìm thẻ div có chứa nhiều chữ nhất
        if not main_content:
            best_div, max_len = None, 0
            for div in soup.find_all('div'):
                div_id = div.get('id', '') or ''
                div_class = ' '.join(div.get('class', [])) if div.get('class') else ''
                if any(x in div_id.lower() or x in div_class.lower() for x in ['header', 'footer', 'nav', 'sidebar', 'comment', 'ads']):
                    continue
                txt_len = len(div.get_text().strip())
                if txt_len > max_len:
                    max_len = txt_len
                    best_div = div
            if best_div and max_len > 200:
                main_content = best_div

        if main_content:
            # Dọn dẹp rác HTML không cần thiết
            for trash in main_content.find_all(['script', 'style', 'iframe', 'ins', 'a', 'button']):
                trash.decompose()

            # Loại bỏ các phần tử bị ẩn bằng CSS (kỹ thuật chèn nội dung trùng
            # để chống crawl) - nếu không xoá, get_text()/find_all('p') vẫn đọc
            # được nội dung ẩn này và gây trùng lặp câu.
            for hidden in main_content.select(
                '[style*="display:none"], [style*="display: none"], '
                '[style*="visibility:hidden"], [style*="visibility: hidden"], '
                '.hidden, .d-none, [hidden]'
            ):
                hidden.decompose()

            p_tags = main_content.find_all('p')
            lines = []
            
            if p_tags and len(p_tags) > 3:
                for p in p_tags:
                    p_text = p.get_text().strip()
                    if not p_text or any(x in p_text.lower() for x in ["chương trước", "mục lục", "chương sau", "cỡ chữ"]):
                        continue
                    lines.append(p_text)

                # Loại bỏ câu bị lặp lại liên tiếp (nguyên nhân chính gây trùng lặp)
                lines = dedupe_consecutive_lines(lines)

                # Áp dụng bộ lọc dọn rác/Donate
                lines = clean_junk_text(lines)
                content = "\n\n".join(lines).strip()
            
            # Fallback lấy text thuần nếu không bóc tách được qua thẻ <p>
            if not content or len(content) < 100:
                raw_text = main_content.get_text(separator="\n")
                raw_lines = []
                for line in raw_text.split('\n'):
                    l_str = line.strip()
                    if not l_str or any(x in l_str.lower() for x in [
                        "chương trước", "mục lục", "chương sau", "cỡ chữ", "phông chữ", 
                        "times new roman", "palatino", "giãn dòng", "màu nền"
                    ]):
                        continue
                    raw_lines.append(l_str)

                # Loại bỏ câu bị lặp lại liên tiếp (nguyên nhân chính gây trùng lặp)
                raw_lines = dedupe_consecutive_lines(raw_lines)

                # Áp dụng bộ lọc dọn rác/Donate
                raw_lines = clean_junk_text(raw_lines)
                content = "\n\n".join(raw_lines).strip()

        # 3. TÌM LINK CHƯƠNG TIẾP THEO
        for a_tag in soup.find_all('a', href=True):
            a_text = a_tag.get_text().strip().lower()
            a_class = " ".join(a_tag.get('class', [])).lower()
            a_id = a_tag.get('id', '').lower()
            
            if 'next' in a_id or 'next' in a_class or 'btn-next' in a_class:
                next_url = a_tag['href']
                break
            if 'chương sau' in a_text or 'chương tiếp' in a_text or a_text in ['>', '›', '»', 'sau']:
                next_url = a_tag['href']
                break
                
        # Tự động tính toán link tiếp theo nếu không thấy nút bấm
        if not next_url:
            current_num = get_chapter_num_from_url(url, START_CHAPTER_NUM)
            next_num = current_num + 1
            next_url = re.sub(r'chuong-\d+', f'chuong-{next_num}', url, flags=re.IGNORECASE)

        if next_url and not next_url.startswith('http'):
            next_url = BASE_URL + next_url if next_url.startswith('/') else BASE_URL + '/' + next_url
                
    except Exception as e:
        print(f"[-] Lỗi kết nối/hệ thống: {e}")
        
    return clean_title, content, next_url

def start_leech():
    current_url = START_URL
    chapter_count = 0
    consecutive_skips = 0
    
    # Khôi phục tiến trình cào cũ nếu file save_progress.txt tồn tại
    if os.path.exists(LOG_FILE):
        with open(LOG_FILE, 'r', encoding='utf-8') as f:
            lines = f.read().splitlines()
            if len(lines) >= 2:
                current_url = lines[0].strip()
                try: 
                    chapter_count = int(lines[1].strip())
                except ValueError: 
                    chapter_count = 0
        print(f"[+] Tìm thấy tiến trình cũ. Đang chạy tiếp từ URL: {current_url} (Chương thứ {chapter_count + 1})")

    while current_url:
        chapter_count += 1
        part_number = ((chapter_count - 1) // 500) + 1
        output_file = f"{PREFIX_NAME}_Phan_{part_number}.txt"
        
        # Đồng bộ số chương thực tế từ URL
        real_chapter_num = get_chapter_num_from_url(current_url, START_CHAPTER_NUM + chapter_count - 1)
        print(f"[+] [Phần {part_number}] Đang tải: {current_url}")
        
        content = ""
        clean_title = ""
        next_url = None
        
        # Vòng lặp Retry cho từng chương
        for attempt in range(1, MAX_RETRIES_PER_CHAPTER + 1):
            clean_title, content, next_url = parse_chapter(current_url)
            
            # Nếu gặp 404 (Hết truyện) -> Dừng hẳn
            if clean_title is None and content is None and next_url is None:
                print("[✔] Hoàn thành: Đã cào tới chương cuối cùng của truyện!")
                return

            if content and len(content) > 100:
                break  # Tải thành công -> Thoát vòng lặp retry
            
            print(f"[!] Thử lại lần {attempt}/{MAX_RETRIES_PER_CHAPTER} sau 5s...")
            time.sleep(5)

        # Xử lý kết quả cào được
        if content and len(content) > 100:
            consecutive_skips = 0
            if not clean_title:
                clean_title = f"Chương {real_chapter_num}"

            # Ghi vào file output
            with open(output_file, 'a', encoding='utf-8') as f:
                f.write(f"\n\n=== {clean_title} ===\n\n")
                f.write(content)
            
            # Ghi tiến trình vào file log
            with open(LOG_FILE, 'w', encoding='utf-8') as f:
                f.write(f"{next_url}\n{chapter_count}")
            
            print(f"[✔] Đã lưu thành công: {clean_title} ({len(content)} ký tự - Đã lọc rác)")
            current_url = next_url
        else:
            consecutive_skips += 1
            print(f"[-] Không thể lấy nội dung chương này sau {MAX_RETRIES_PER_CHAPTER} lần thử.")
            
            # Tự động nhảy sang URL chương kế tiếp
            next_num = real_chapter_num + 1
            current_url = re.sub(r'chuong-\d+', f'chuong-{next_num}', current_url, flags=re.IGNORECASE)
            print(f"[→] Tự động bỏ qua và chuyển sang link: {current_url}")

            if consecutive_skips >= 3:
                print("[✔] Bị lỗi 3 chương liên tiếp. Dừng tiến trình để kiểm tra lại!")
                break

        # Tạm nghỉ ngẫu nhiên từ 2.5s đến 4s để giả lập thao tác người thật
        time.sleep(random.uniform(2.5, 4.0))

if __name__ == "__main__":
    start_leech()
