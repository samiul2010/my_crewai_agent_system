"""
media_tools.py — Media Download & Processing Tools
==================================================
Agent বিভিন্ন সোশ্যাল মিডিয়া প্ল্যাটফর্ম থেকে ভিডিও/অডিও/ইমেজ ডাউনলোড,
সোশ্যাল মেট্রিক্স এনালাইসিস, কপিরাইট চেক, এবং ভিডিও/ইমেজ প্রসেসিং করতে পারে।

CrewAI @tool ডেকোরেটর ব্যবহার করে তৈরি করা হয়েছে।
"""

import os
import json
import requests
import yt_dlp
from crewai.tools import tool
from bs4 import BeautifulSoup
from urllib.parse import urlparse
import time
from typing import Optional


try:
    from tools import WORKSPACE
    DEFAULT_DOWNLOAD_DIR = os.path.join(WORKSPACE, "downloads")
except ImportError:
    DEFAULT_DOWNLOAD_DIR = os.getenv("MEDIA_DOWNLOAD_DIR", "/tmp/media_downloads")

try:
    os.makedirs(DEFAULT_DOWNLOAD_DIR, exist_ok=True)
except PermissionError:
    import tempfile
    DEFAULT_DOWNLOAD_DIR = tempfile.mkdtemp(prefix="media_downloads_")
    

# ============================================
# টুল ১: ইউনিভার্সাল মিডিয়া ডাউনলোডার
# ============================================
@tool("universal_media_downloader")
def universal_media_downloader(url: str, save_path: str = "") -> str:
    """
    যেকোনো সোশ্যাল মিডিয়া প্ল্যাটফর্ম (YouTube, Facebook, Instagram, TikTok, 
    Twitter/X, Vimeo, Dailymotion, Twitch, SoundCloud, Spotify) থেকে 
    ভিডিও, অডিও বা ইমেজ ডাউনলোড করে।
    
    Args:
        url: মিডিয়ার URL
        save_path: ফাইল সংরক্ষণের পথ (default: "./downloads/")
    
    Returns:
        ডাউনলোডকৃত ফাইলের তথ্য এবং অবস্থান
    """
    try:
        # ডাউনলোড ডিরেক্টরি তৈরি
        target_dir = save_path.strip() if save_path and save_path.strip() else DEFAULT_DOWNLOAD_DIR
        try:
            os.makedirs(target_dir, exist_ok=True)
        except PermissionError:
            target_dir = DEFAULT_DOWNLOAD_DIR
            os.makedirs(target_dir, exist_ok=True)
        save_path = target_dir
        
        
        # অডিও শুধু চেক
        audio_patterns = ['soundcloud', 'spotify', 'audiomack', 'bandcamp']
        is_audio = any(pattern in url.lower() for pattern in audio_patterns)
        
        # yt-dlp কনফিগারেশন
        ydl_opts = {
            'outtmpl': f'{save_path}/%(title)s.%(ext)s',
            'quiet': True,
            'no_warnings': True,
            'ignoreerrors': True,
            'extract_flat': False,
        }
        
        if is_audio:
            ydl_opts.update({
                'format': 'bestaudio/best',
                'postprocessors': [{
                    'key': 'FFmpegExtractAudio',
                    'preferredcodec': 'mp3',
                    'preferredquality': '192',
                }]
            })
        else:
            ydl_opts.update({
                'format': 'bestvideo+bestaudio/best',
                'merge_output_format': 'mp4',
                'postprocessors': [{
                    'key': 'FFmpegVideoConvertor',
                    'preferedformat': 'mp4',
                }]
            })
        
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)
            
            if info is None:
                return "❌ URL থেকে তথ্য সংগ্রহ করা যায়নি।"
            
            file_info = {
                'title': info.get('title', 'Unknown'),
                'ext': info.get('ext', 'unknown'),
                'duration': info.get('duration', 0),
                'view_count': info.get('view_count', 0),
                'like_count': info.get('like_count', 0),
                'comment_count': info.get('comment_count', 0),
                'uploader': info.get('uploader', 'Unknown'),
                'upload_date': info.get('upload_date', 'Unknown'),
            }
            
            return json.dumps({
                'status': 'success',
                'message': f"✅ ডাউনলোড সফল: {file_info['title']}",
                'file_info': file_info,
                'file_path': f"{save_path}/{file_info['title']}.{file_info['ext']}"
            }, indent=2, ensure_ascii=False)
            
    except Exception as e:
        return json.dumps({
            'status': 'error',
            'message': f"❌ ডাউনলোড ব্যর্থ: {str(e)}"
        }, indent=2, ensure_ascii=False)


# ============================================
# টুল ২: সোশ্যাল মিডিয়া অ্যানালিটিক্স
# ============================================
@tool("social_media_analytics")
def social_media_analytics(url: str) -> str:
    """
    যেকোনো সোশ্যাল মিডিয়া পোস্টের লাইক, কমেন্ট, শেয়ার, ভিউ এবং 
    এনগেজমেন্ট মেট্রিক্স সংগ্রহ করে।
    
    Args:
        url: সোশ্যাল মিডিয়া পোস্টের URL
    
    Returns:
        লাইক, কমেন্ট, শেয়ার, ভিউ এবং এনগেজমেন্ট স্কোর
    """
    try:
        # প্ল্যাটফর্ম ডিটেক্ট
        platforms = {
            'youtube.com': 'youtube', 'youtu.be': 'youtube',
            'facebook.com': 'facebook', 'fb.watch': 'facebook',
            'instagram.com': 'instagram',
            'twitter.com': 'twitter', 'x.com': 'twitter',
            'tiktok.com': 'tiktok',
            'linkedin.com': 'linkedin'
        }
        
        platform = 'unknown'
        for domain, plat in platforms.items():
            if domain in url.lower():
                platform = plat
                break
        
        # YouTube মেট্রিক্স
        if platform == 'youtube':
            with yt_dlp.YoutubeDL({'quiet': True, 'extract_flat': True}) as ydl:
                info = ydl.extract_info(url, download=False)
                if info:
                    views = info.get('view_count', 0)
                    likes = info.get('like_count', 0)
                    comments = info.get('comment_count', 0)
                    
                    # এনগেজমেন্ট স্কোর
                    engagement = 0.0
                    if views > 0:
                        engagement = round(min((likes * 1.0 + comments * 2.0) / views * 100, 100), 2)
                    
                    return json.dumps({
                        'status': 'success',
                        'platform': 'YouTube',
                        'title': info.get('title', 'N/A'),
                        'views': views,
                        'likes': likes,
                        'comments': comments,
                        'uploader': info.get('uploader', 'N/A'),
                        'engagement_score': engagement
                    }, indent=2, ensure_ascii=False)
        
        # অন্যান্য প্ল্যাটফর্মের জন্য ডেমো ডেটা
        demo_data = {
            'facebook': {'likes': 1234, 'comments': 56, 'shares': 78, 'engagement_score': 85.5},
            'instagram': {'likes': 2456, 'comments': 123, 'shares': 45, 'engagement_score': 92.5},
            'twitter': {'likes': 567, 'comments': 89, 'retweets': 234, 'views': 12345, 'engagement_score': 78.5},
            'tiktok': {'views': 123456, 'likes': 12345, 'comments': 1234, 'shares': 567, 'engagement_score': 95.5},
        }
        
        if platform in demo_data:
            return json.dumps({
                'status': 'warning',
                'platform': platform.capitalize(),
                'message': f"⚠️ {platform.capitalize()} API প্রয়োজন। ডেমো ডেটা দেখাচ্ছি...",
                'demo_metrics': demo_data[platform]
            }, indent=2, ensure_ascii=False)
        
        return json.dumps({
            'status': 'error',
            'message': f"❌ {platform} প্ল্যাটফর্ম সাপোর্ট করে না।"
        }, indent=2, ensure_ascii=False)
        
    except Exception as e:
        return json.dumps({
            'status': 'error',
            'message': f"❌ মেট্রিক্স সংগ্রহ ব্যর্থ: {str(e)}"
        }, indent=2, ensure_ascii=False)


# ============================================
# টুল ৩: কপিরাইট চেকার
# ============================================
@tool("copyright_checker")
def copyright_checker(url: str) -> str:
    """
    মিডিয়া ফাইলের কপিরাইট স্ট্যাটাস চেক করে।
    Creative Commons, Public Domain, Royalty-Free লাইসেন্স চিহ্নিত করে।
    
    Args:
        url: মিডিয়া ফাইলের URL
    
    Returns:
        কপিরাইট স্ট্যাটাস এবং লাইসেন্স তথ্য
    """
    try:
        domain = urlparse(url).netloc.lower()
        
        free_platforms = {
            'pexels.com': 'Pexels License (Free)',
            'pixabay.com': 'Pixabay License (Free)',
            'unsplash.com': 'Unsplash License (Free)',
            'videvo.net': 'Videvo License (Free)',
            'mixkit.co': 'Mixkit License (Free)',
            'coverr.co': 'Coverr License (Free)',
            'freesound.org': 'Creative Commons',
            'freesfx.co.uk': 'Royalty-Free',
        }
        
        is_free = False
        license_type = 'Unknown'
        
        for plat, lic in free_platforms.items():
            if plat in domain:
                is_free = True
                license_type = lic
                break
        
        return json.dumps({
            'status': 'success',
            'url': url,
            'domain': domain,
            'copyright_status': '✅ Free (কপিরাইট-মুক্ত)' if is_free else '⚠️ Unknown (অজানা)',
            'recommendation': '✅ ব্যবহারযোগ্য' if is_free else '⚠️ ব্যবহারের আগে লাইসেন্স যাচাই করুন',
            'license_type': license_type,
            'free_platforms': list(free_platforms.keys())
        }, indent=2, ensure_ascii=False)
        
    except Exception as e:
        return json.dumps({
            'status': 'error',
            'message': f"❌ কপিরাইট চেক ব্যর্থ: {str(e)}"
        }, indent=2, ensure_ascii=False)


# ============================================
# টুল ৪: ভিডিও থাম্বনেইল এক্সট্র্যাক্টর
# ============================================
@tool("extract_thumbnail")
def extract_thumbnail(video_path: str) -> str:
    """
    ভিডিও ফাইল থেকে থাম্বনেইল (প্রথম ফ্রেম) এক্সট্র্যাক্ট করে।
    
    Args:
        video_path: ভিডিও ফাইলের সম্পূর্ণ পথ
    
    Returns:
        থাম্বনেইল ফাইলের অবস্থান
    """
    try:
        if not os.path.exists(video_path):
            return json.dumps({
                'status': 'error',
                'message': f"❌ ফাইল পাওয়া যায়নি: {video_path}"
            }, indent=2, ensure_ascii=False)
        
        import cv2
        video = cv2.VideoCapture(video_path)
        success, frame = video.read()
        video.release()
        
        if success:
            output_path = os.path.splitext(video_path)[0] + "_thumbnail.jpg"
            cv2.imwrite(output_path, frame)
            return json.dumps({
                'status': 'success',
                'message': f"✅ থাম্বনেইল তৈরি করা হয়েছে: {output_path}",
                'file_path': output_path
            }, indent=2, ensure_ascii=False)
        else:
            return json.dumps({
                'status': 'error',
                'message': "❌ থাম্বনেইল এক্সট্র্যাক্ট করা যায়নি।"
            }, indent=2, ensure_ascii=False)
            
    except ImportError:
        return json.dumps({
            'status': 'error',
            'message': "❌ OpenCV ইনস্টল করা নেই। `pip install opencv-python` রান করুন।"
        }, indent=2, ensure_ascii=False)
    except Exception as e:
        return json.dumps({
            'status': 'error',
            'message': f"❌ থাম্বনেইল এক্সট্র্যাক্ট ব্যর্থ: {str(e)}"
        }, indent=2, ensure_ascii=False)


# ============================================
# টুল ৫: অডিও এক্সট্র্যাক্টর
# ============================================
@tool("extract_audio_from_video")
def extract_audio_from_video(video_path: str) -> str:
    """
    ভিডিও ফাইল থেকে অডিও এক্সট্র্যাক্ট করে MP3 ফরম্যাটে সংরক্ষণ করে।
    
    Args:
        video_path: ভিডিও ফাইলের সম্পূর্ণ পথ
    
    Returns:
        অডিও ফাইলের অবস্থান
    """
    try:
        if not os.path.exists(video_path):
            return json.dumps({
                'status': 'error',
                'message': f"❌ ফাইল পাওয়া যায়নি: {video_path}"
            }, indent=2, ensure_ascii=False)
        
        import subprocess
        output_path = os.path.splitext(video_path)[0] + "_audio.mp3"
        
        command = [
            'ffmpeg', '-i', video_path,
            '-vn', '-acodec', 'mp3',
            '-ab', '192k', output_path
        ]
        subprocess.run(command, check=True, capture_output=True)
        
        return json.dumps({
            'status': 'success',
            'message': f"✅ অডিও এক্সট্র্যাক্ট করা হয়েছে: {output_path}",
            'file_path': output_path
        }, indent=2, ensure_ascii=False)
        
    except FileNotFoundError:
        return json.dumps({
            'status': 'error',
            'message': "❌ FFmpeg পাওয়া যায়নি। FFmpeg ইনস্টল করুন।"
        }, indent=2, ensure_ascii=False)
    except Exception as e:
        return json.dumps({
            'status': 'error',
            'message': f"❌ অডিও এক্সট্র্যাক্ট ব্যর্থ: {str(e)}"
        }, indent=2, ensure_ascii=False)


# ============================================
# টুল ৬: ইমেজ রিসাইজার
# ============================================
@tool("resize_image")
def resize_image(image_path: str, width: int = 1920, height: int = 1080) -> str:
    """
    ইমেজ ফাইলকে নির্দিষ্ট মাপে রিসাইজ করে।
    
    Args:
        image_path: ইমেজ ফাইলের সম্পূর্ণ পথ
        width: প্রস্থ (default: 1920)
        height: উচ্চতা (default: 1080)
    
    Returns:
        রিসাইজ করা ইমেজের অবস্থান
    """
    try:
        if not os.path.exists(image_path):
            return json.dumps({
                'status': 'error',
                'message': f"❌ ফাইল পাওয়া যায়নি: {image_path}"
            }, indent=2, ensure_ascii=False)
        
        from PIL import Image
        img = Image.open(image_path)
        resized = img.resize((width, height))
        
        output_path = os.path.splitext(image_path)[0] + f"_{width}x{height}.jpg"
        resized.save(output_path, quality=95)
        
        return json.dumps({
            'status': 'success',
            'message': f"✅ ইমেজ রিসাইজ করা হয়েছে: {output_path}",
            'file_path': output_path,
            'new_size': {'width': width, 'height': height}
        }, indent=2, ensure_ascii=False)
        
    except ImportError:
        return json.dumps({
            'status': 'error',
            'message': "❌ Pillow ইনস্টল করা নেই। `pip install Pillow` রান করুন।"
        }, indent=2, ensure_ascii=False)
    except Exception as e:
        return json.dumps({
            'status': 'error',
            'message': f"❌ ইমেজ রিসাইজ ব্যর্থ: {str(e)}"
        }, indent=2, ensure_ascii=False)


# ============================================
# টুল ৭: ফাইল ম্যানেজমেন্ট
# ============================================
@tool("file_management")
def file_management(action: str, source: str, destination: Optional[str] = None) -> str:
    """
    ফাইল ম্যানেজমেন্ট অপারেশন: rename, move, delete, info
    
    Args:
        action: 'rename', 'move', 'delete', 'info'
        source: সোর্স ফাইলের পথ
        destination: গন্তব্য পথ (rename/move এর জন্য প্রয়োজন)
    
    Returns:
        অপারেশনের ফলাফল
    """
    try:
        if not os.path.exists(source):
            return json.dumps({
                'status': 'error',
                'message': f"❌ সোর্স ফাইল পাওয়া যায়নি: {source}"
            }, indent=2, ensure_ascii=False)
        
        if action == 'rename':
            if not destination:
                return json.dumps({
                    'status': 'error',
                    'message': "❌ rename এর জন্য destination প্রয়োজন"
                }, indent=2, ensure_ascii=False)
            os.rename(source, destination)
            return json.dumps({
                'status': 'success',
                'message': f"✅ ফাইলের নাম পরিবর্তন: {destination}"
            }, indent=2, ensure_ascii=False)
            
        elif action == 'move':
            if not destination:
                return json.dumps({
                    'status': 'error',
                    'message': "❌ move এর জন্য destination প্রয়োজন"
                }, indent=2, ensure_ascii=False)
            dest_dir = os.path.dirname(destination)
            if dest_dir:
                try:
                    os.makedirs(dest_dir, exist_ok=True)
                except PermissionError:
                    return json.dumps({
                        'status': 'error',
                        'message': f"❌ গন্তব্য ফোল্ডার তৈরির অনুমতি নেই: {dest_dir}। "
                                    f"DEFAULT_DOWNLOAD_DIR ({DEFAULT_DOWNLOAD_DIR}) এর "
                                    f"ভেতরে কোনো পথ ব্যবহার করুন।"
                    }, indent=2, ensure_ascii=False)
            os.rename(source, destination)
            return json.dumps({
                'status': 'success',
                'message': f"✅ ফাইল মুভ: {destination}"
            }, indent=2, ensure_ascii=False)
            
        elif action == 'delete':
            os.remove(source)
            return json.dumps({
                'status': 'success',
                'message': f"✅ ফাইল ডিলিট: {source}"
            }, indent=2, ensure_ascii=False)
            
        elif action == 'info':
            stats = os.stat(source)
            return json.dumps({
                'status': 'success',
                'file_info': {
                    'name': os.path.basename(source),
                    'size': stats.st_size,
                    'size_mb': round(stats.st_size / (1024 * 1024), 2),
                    'created': time.ctime(stats.st_ctime),
                    'modified': time.ctime(stats.st_mtime)
                }
            }, indent=2, ensure_ascii=False)
        else:
            return json.dumps({
                'status': 'error',
                'message': f"❌ অজানা অ্যাকশন: {action}"
            }, indent=2, ensure_ascii=False)
            
    except Exception as e:
        return json.dumps({
            'status': 'error',
            'message': f"❌ ফাইল ম্যানেজমেন্ট ব্যর্থ: {str(e)}"
        }, indent=2, ensure_ascii=False)

