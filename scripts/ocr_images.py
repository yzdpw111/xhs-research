#!/usr/bin/env python3
"""
ocr_images.py — 对图片目录批量 OCR（RapidOCR）

模块接口: ocr_directory(img_dir) -> [{"file","lines","text"}, ...]
CLI 兼容: python ocr_images.py <img_dir>  → stdout JSON {"images":[...]}
"""
import json
import os
import sys

EXTS = (".jpg", ".jpeg", ".png", ".webp", ".bmp")

# RapidOCR 模块级惰性单例：多篇笔记/多次调用只加载一次模型
_OCR = None


def _get_ocr():
    """返回全局 RapidOCR 实例（首次调用时懒加载），未安装时返回 None"""
    global _OCR
    if _OCR is None:
        try:
            from rapidocr_onnxruntime import RapidOCR
        except ImportError:
            return None
        _OCR = RapidOCR()
    return _OCR


def ocr_directory(img_dir):
    ocr = _get_ocr()
    if ocr is None:
        return {"error": "rapidocr_onnxruntime not installed: pip install -r requirements.txt"}
    images = []
    for name in sorted(os.listdir(img_dir)):
        if not name.lower().endswith(EXTS):
            continue
        path = os.path.join(img_dir, name)
        try:
            res, _ = ocr(path)
            lines = [item[1] for item in res] if res else []
            images.append({"file": name, "lines": len(lines), "text": "\n".join(lines)})
        except Exception as e:
            images.append({"file": name, "error": str(e)})
    return {"images": images}


if __name__ == "__main__":
    if len(sys.argv) < 2:
        sys.stdout.write(json.dumps({"error": "Usage: python ocr_images.py <img_dir>"}))
        sys.exit(1)
    sys.stdout.write(json.dumps(ocr_directory(sys.argv[1]), ensure_ascii=False))
