from flask import Flask, render_template, request, jsonify, send_file
import requests
from bs4 import BeautifulSoup
from urllib.parse import urljoin
from concurrent.futures import ThreadPoolExecutor
from typing import List, Dict
import re
import io
import zipfile
from datetime import datetime
import os

app = Flask(__name__)

def is_likely_logo(img_tag, url: str) -> bool:
    """
    More strict logo detection with enhanced heuristics.
    """
    src = img_tag.get('src', '').lower()
    alt = img_tag.get('alt', '').lower()
    class_list = ' '.join(img_tag.get('class', [])).lower()
    img_id = img_tag.get('id', '').lower()
    parent_class = ' '.join(img_tag.parent.get('class', [])).lower() if img_tag.parent else ''
    parent_id = img_tag.parent.get('id', '').lower() if img_tag.parent else ''
    
    # Strong logo indicators
    strong_logo_keywords = [
        'logo', 'brand-logo', 'site-logo', 'company-logo',
        'header-logo', 'main-logo', 'navbar-logo'
    ]
    
    # Weak indicators that need additional confirmation
    weak_logo_keywords = [
        'brand', 'company', 'header-image', 'nav-image'
    ]
    
    # Negative indicators that suggest it's not a logo
    negative_indicators = [
        'product', 'avatar', 'thumbnail', 'banner', 'ad-', 
        'slider', 'carousel', 'gallery', 'icon-', 'social'
    ]
    
    # Check for negative indicators first
    if any(neg in text for neg in negative_indicators 
           for text in [src, alt, class_list, img_id, parent_class, parent_id]):
        return False
    
    # Get image dimensions if available
    width = img_tag.get('width', '').strip().rstrip('px')
    height = img_tag.get('height', '').strip().rstrip('px')
    
    # Check image dimensions if available
    try:
        if width and height:
            w, h = int(width), int(height)
            # Logos typically aren't very large or very small
            if w > 400 or h > 200 or (w < 20 or h < 20):
                return False
            # Logos typically aren't perfect squares or very elongated
            aspect_ratio = w / h
            if aspect_ratio > 4 or aspect_ratio < 0.25:
                return False
    except ValueError:
        pass
    
    # Check for strong logo indicators
    if any(keyword in text for keyword in strong_logo_keywords 
           for text in [src, alt, class_list, img_id, parent_class, parent_id]):
        return True
    
    # Check for header/navigation placement
    header_parent = img_tag.find_parent(['header', 'nav'])
    if header_parent:
        # Check if it's the first or only image in the header/nav
        header_images = header_parent.find_all('img')
        if img_tag == header_images[0]:
            return True
    
    return False

def extract_logo(url: str) -> List[str]:
    """
    Extract likely logo images from a website.
    """
    try:
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
        }
        response = requests.get(url, headers=headers, timeout=10)
        response.raise_for_status()
        
        soup = BeautifulSoup(response.text, 'html.parser')
        images = soup.find_all('img')
        
        logo_urls = []
        
        for img in images:
            if is_likely_logo(img, url):
                src = img.get('src')
                if src:
                    absolute_url = urljoin(url, src)
                    if absolute_url not in logo_urls:
                        logo_urls.append(absolute_url)
        
        return logo_urls
        
    except Exception as e:
        print(f"Error processing {url}: {str(e)}")
        return []

def extract_logos_from_sites(urls: List[str], max_workers: int = 5) -> Dict[str, List[str]]:
    """
    Extract logos from multiple websites concurrently.
    """
    results = {}
    
    if not urls:
        return results
    
    if isinstance(urls, str):
        urls = [urls]
    
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        future_to_url = {
            executor.submit(extract_logo, url): url 
            for url in urls
        }
        
        for future in future_to_url:
            url = future_to_url[future]
            try:
                logo_urls = future.result()
                results[url] = logo_urls
            except Exception as e:
                print(f"Error processing {url}: {str(e)}")
                results[url] = []
    
    return results

# Routes
@app.route('/')
def home():
    return render_template('index.html')

@app.route('/extract', methods=['POST'])
def extract():
    urls = request.form.get('urls', '').split('\n')
    urls = [url.strip() for url in urls if url.strip()]
    
    if not urls:
        return jsonify({'error': 'No valid URLs provided'})
    
    results = extract_logos_from_sites(urls)
    return jsonify(results)

@app.route('/download-image', methods=['POST'])
def download_image():
    try:
        image_url = request.json.get('url')
        brand_name = request.json.get('brand_name', 'logo')
        index = request.json.get('index', '')
        
        if not image_url:
            return jsonify({'error': 'No URL provided'}), 400

        # Get the image
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
        }
        response = requests.get(image_url, headers=headers, stream=True)
        response.raise_for_status()

        # Get extension from URL or content-type
        ext = image_url.split('.')[-1].split('?')[0]
        if not ext or len(ext) > 4:
            content_type = response.headers.get('content-type', '')
            ext = content_type.split('/')[-1] if '/' in content_type else 'png'

        # Create filename with brand name and index
        filename = f"{brand_name}{'-' + str(index) if index else ''}.{ext}"

        # Create file-like object in memory
        img_io = io.BytesIO(response.content)
        img_io.seek(0)

        return send_file(
            img_io,
            mimetype=response.headers.get('content-type', 'image/png'),
            as_attachment=True,
            download_name=filename
        )

    except Exception as e:
        print(f"Error downloading image: {str(e)}")
        return jsonify({'error': 'Failed to download image'}), 500

@app.route('/download-all', methods=['POST'])
def download_all():
    try:
        logos_data = request.json
        if not logos_data:
            return jsonify({'error': 'No logos provided'}), 400

        temp_dir = 'temp_downloads'
        if not os.path.exists(temp_dir):
            os.makedirs(temp_dir)

        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        zip_filename = f'logos_{timestamp}.zip'
        zip_path = os.path.join(temp_dir, zip_filename)

        with zipfile.ZipFile(zip_path, 'w') as zipf:
            for brand_name, logo_urls in logos_data.items():
                folder_name = "".join(c if c.isalnum() or c in (' ', '-', '_') else '_' for c in brand_name)
                folder_name = folder_name.strip()
                
                for index, url in enumerate(logo_urls):
                    try:
                        headers = {
                            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
                        }
                        response = requests.get(url, headers=headers)
                        response.raise_for_status()

                        # Get extension from URL or content-type
                        ext = url.split('.')[-1].split('?')[0]
                        if not ext or len(ext) > 4:
                            content_type = response.headers.get('content-type', '')
                            ext = content_type.split('/')[-1] if '/' in content_type else 'png'

                        filename = f"{folder_name}/logo{'-' + str(index + 1) if len(logo_urls) > 1 else ''}.{ext}"
                        zipf.writestr(filename, response.content)
                        
                    except Exception as e:
                        print(f"Error downloading {url}: {str(e)}")
                        continue

        return send_file(
            zip_path,
            mimetype='application/zip',
            as_attachment=True,
            download_name=zip_filename
        )

    except Exception as e:
        print(f"Error creating zip: {str(e)}")
        return jsonify({'error': 'Failed to create zip file'}), 500
    finally:
        try:
            if os.path.exists(zip_path):
                os.remove(zip_path)
        except:
            pass

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port)