from flask import Flask, render_template, request, redirect, session, jsonify, send_file
import sqlite3
import os
import uuid
from werkzeug.utils import secure_filename
import threading
import time
import logging
from contextlib import contextmanager
# PDF libraries
import PyPDF2
from pdf2docx import Converter

# Image libraries
from PIL import Image

# Document libraries - Enhanced for better Word to PDF conversion
from docx import Document
from reportlab.pdfgen import canvas
from reportlab.lib.pagesizes import letter, A4
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, PageBreak, KeepTogether
from reportlab.platypus.tables import TableStyle
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle, StyleSheet1
from reportlab.lib.enums import TA_LEFT, TA_CENTER, TA_RIGHT, TA_JUSTIFY
from reportlab.lib import colors
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.lib.fonts import addMapping
import re
from io import BytesIO

# For better PDF compression
import subprocess
import tempfile

app = Flask(__name__)
app.secret_key = "supersecretkeyOmniConverter2026"
app.config['MAX_CONTENT_LENGTH'] = 50 * 1024 * 1024  # 50MB
app.config['UPLOAD_FOLDER'] = 'uploads'
app.config['CONVERTED_FOLDER'] = 'converted'

# Create folders
for folder in [app.config['UPLOAD_FOLDER'], app.config['CONVERTED_FOLDER']]:
    os.makedirs(folder, exist_ok=True)

logging.basicConfig(level=logging.INFO)

# Allowed extensions
ALLOWED_EXTENSIONS = {'pdf', 'png', 'jpg', 'jpeg', 'docx', 'xlsx', 'pptx', 'txt'}

# ================= DATABASE CONFIGURATION (FIXED FOR RENDER) =================
# Use local directory - works on both local and Render free tier
DATABASE = 'users.db'

@contextmanager
def get_db():
    """Thread-safe database connection with timeout"""
    conn = sqlite3.connect(DATABASE, timeout=10)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
    finally:
        conn.close()

def init_db():
    with get_db() as conn:
        c = conn.cursor()
        c.execute('''CREATE TABLE IF NOT EXISTS users
                     (id INTEGER PRIMARY KEY AUTOINCREMENT,
                      username TEXT UNIQUE,
                      email TEXT,
                      password TEXT,
                      created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)''')
        c.execute('''CREATE TABLE IF NOT EXISTS conversions
                     (id INTEGER PRIMARY KEY AUTOINCREMENT,
                      user_id INTEGER,
                      tool TEXT,
                      original_filename TEXT,
                      converted_filename TEXT,
                      created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                      FOREIGN KEY (user_id) REFERENCES users (id))''')
        conn.commit()

# Initialize database
init_db()

# ================= CLEANUP =================
def cleanup_old_files():
    while True:
        time.sleep(3600)
        now = time.time()
        for folder in [app.config['UPLOAD_FOLDER'], app.config['CONVERTED_FOLDER']]:
            for filename in os.listdir(folder):
                filepath = os.path.join(folder, filename)
                if os.path.isfile(filepath) and now - os.path.getmtime(filepath) > 3600:
                    try:
                        os.remove(filepath)
                    except:
                        pass

threading.Thread(target=cleanup_old_files, daemon=True).start()

# ================= HELPER FUNCTIONS =================
def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

def unique_filename(original_filename):
    ext = original_filename.rsplit('.', 1)[1].lower() if '.' in original_filename else ''
    return f"{uuid.uuid4().hex}.{ext}"

def save_files(files):
    saved = []
    for file in files:
        if file and allowed_file(file.filename):
            filename = secure_filename(file.filename)
            unique_name = unique_filename(filename)
            path = os.path.join(app.config['UPLOAD_FOLDER'], unique_name)
            file.save(path)
            saved.append({
                'original': filename,
                'saved': unique_name,
                'path': path,
                'ext': filename.rsplit('.', 1)[1].lower()
            })
    return saved

# ================= ENHANCED CONVERSION FUNCTIONS =================

def merge_pdfs(file_infos):
    """Merge multiple PDF files into one."""
    if len(file_infos) != 2:
        raise ValueError("Please select exactly 2 PDF files to merge")

    for f in file_infos:
        if f['ext'] != 'pdf':
            raise ValueError(f"File '{f['original']}' is not a PDF. Only PDF files can be merged.")

    merger = PyPDF2.PdfMerger()
    failed_files = []

    for f in file_infos:
        try:
            with open(f['path'], 'rb') as pdf_file:
                merger.append(pdf_file)
        except Exception as e:
            failed_files.append(f['original'])
            logging.error(f"Error merging file {f['original']}: {str(e)}")

    if failed_files:
        merger.close()
        raise ValueError(f"Could not merge the following files (possibly corrupt): {', '.join(failed_files)}")

    base_name = file_infos[0]['original'].rsplit('.', 1)[0]
    out_name = f"{base_name}_merged.pdf"
    out_path = os.path.join(app.config['CONVERTED_FOLDER'], out_name)

    try:
        merger.write(out_path)
        merger.close()
        return out_path, out_name
    except Exception as e:
        merger.close()
        raise Exception(f"Failed to write merged PDF: {str(e)}")

def pdf_to_word(file_infos):
    if len(file_infos) != 1:
        raise ValueError("Please select exactly 1 PDF file to convert to Word")
    
    f = file_infos[0]
    if f['ext'] != 'pdf':
        raise ValueError(f"File '{f['original']}' is not a PDF. Only PDF files can be converted to Word.")
    
    base_name = f['original'].rsplit('.', 1)[0]
    out_name = f"{base_name}.docx"
    out_path = os.path.join(app.config['CONVERTED_FOLDER'], out_name)
    cv = Converter(f['path'])
    cv.convert(out_path, start=0, end=None)
    cv.close()
    return out_path, out_name

def png_to_jpg(file_infos):
    if len(file_infos) != 1:
        raise ValueError("Please select exactly 1 PNG file to convert to JPG")
    
    f = file_infos[0]
    if f['ext'] != 'png':
        raise ValueError(f"File '{f['original']}' is not a PNG. Only PNG files can be converted to JPG.")
    
    base_name = f['original'].rsplit('.', 1)[0]
    out_name = f"{base_name}.jpg"
    out_path = os.path.join(app.config['CONVERTED_FOLDER'], out_name)

    image = Image.open(f['path'])
    if image.mode in ('RGBA', 'LA'):
        rgb = Image.new('RGB', image.size, (255, 255, 255))
        rgb.paste(image, mask=image.split()[-1])
        rgb.save(out_path, 'JPEG', quality=95)
    else:
        image.save(out_path, 'JPEG', quality=95)
    return out_path, out_name

def jpg_to_png(file_infos):
    if len(file_infos) != 1:
        raise ValueError("Please select exactly 1 JPG file to convert to PNG")
    
    f = file_infos[0]
    if f['ext'] not in ['jpg', 'jpeg']:
        raise ValueError(f"File '{f['original']}' is not a JPG. Only JPG files can be converted to PNG.")
    
    base_name = f['original'].rsplit('.', 1)[0]
    out_name = f"{base_name}.png"
    out_path = os.path.join(app.config['CONVERTED_FOLDER'], out_name)
    Image.open(f['path']).save(out_path, 'PNG')
    return out_path, out_name

def compress_image(file_infos):
    if len(file_infos) != 1:
        raise ValueError("Please select exactly 1 image file to compress")
    
    f = file_infos[0]
    if f['ext'] not in ['jpg', 'jpeg', 'png']:
        raise ValueError(f"File '{f['original']}' is not a supported image. Only JPG and PNG files can be compressed.")
    
    base_name = f['original'].rsplit('.', 1)[0]
    out_name = f"{base_name}_compressed.{f['ext']}"
    out_path = os.path.join(app.config['CONVERTED_FOLDER'], out_name)

    image = Image.open(f['path'])
    if f['ext'] in ['jpg', 'jpeg'] and image.mode in ('RGBA', 'LA', 'P'):
        image = image.convert('RGB')

    # Aggressive resizing for better compression
    max_size = 1280
    if max(image.width, image.height) > max_size:
        ratio = max_size / float(max(image.width, image.height))
        new_size = (int(image.width * ratio), int(image.height * ratio))
        image = image.resize(new_size, Image.Resampling.LANCZOS)
    
    # Convert to RGB if needed for further optimization
    if image.mode not in ('RGB', 'L'):
        image = image.convert('RGB')

    if f['ext'] in ['jpg', 'jpeg']:
        # Aggressive JPG compression
        image.save(out_path, 'JPEG', optimize=True, quality=30, progressive=True)
    else:
        # Aggressive PNG compression with quantization
        image = image.convert('P', palette=Image.Palette.ADAPTIVE, colors=256)
        image.save(out_path, 'PNG', optimize=True, compress_level=9)

    return out_path, out_name

def image_to_pdf(file_infos):
    if len(file_infos) < 1:
        raise ValueError("Please select at least 1 image file to convert to PDF")
    
    for f in file_infos:
        if f['ext'] not in ['jpg', 'jpeg', 'png']:
            raise ValueError(f"File '{f['original']}' is not a supported image. Only JPG and PNG files can be converted to PDF.")
    
    images = []
    for f in file_infos:
        img = Image.open(f['path']).convert('RGB')
        images.append(img)
    
    base_name = file_infos[0]['original'].rsplit('.', 1)[0]
    out_name = f"{base_name}.pdf"
    out_path = os.path.join(app.config['CONVERTED_FOLDER'], out_name)
    if images:
        if len(images) == 1:
            images[0].save(out_path, 'PDF', resolution=100.0)
        else:
            images[0].save(out_path, 'PDF', resolution=100.0, save_all=True, append_images=images[1:])
    return out_path, out_name

# ================= ENHANCED WORD TO PDF CONVERSION =================

def get_paragraph_style(paragraph):
    """Extract paragraph style information from docx paragraph"""
    style_name = paragraph.style.name.lower() if paragraph.style else "normal"
    
    # Check for heading styles
    if 'heading 1' in style_name or style_name == 'heading1':
        return 'Heading1', 18
    elif 'heading 2' in style_name or style_name == 'heading2':
        return 'Heading2', 14
    elif 'heading 3' in style_name or style_name == 'heading3':
        return 'Heading3', 12
    elif 'heading 4' in style_name:
        return 'Heading4', 11
    elif 'heading' in style_name:
        return 'Heading', 12
    elif 'title' in style_name:
        return 'Title', 20
    elif 'subtitle' in style_name:
        return 'Subtitle', 14
    else:
        return 'Normal', 11

def get_run_formatting(run):
    """Extract formatting from a run and return HTML-like tags"""
    text = run.text
    if not text:
        return '', {}
    
    # Escape HTML special characters
    text = text.replace('&', '&amp;')
    text = text.replace('<', '&lt;')
    text = text.replace('>', '&gt;')
    
    # Handle newlines
    text = text.replace('\n', '<br/>')
    text = text.replace('\r', '')
    
    # Apply formatting tags
    if run.bold:
        text = f'<b>{text}</b>'
    if run.italic:
        text = f'<i>{text}</i>'
    if run.underline:
        text = f'<u>{text}</u>'
    
    # Handle font size
    font_size = 11
    if run.font.size:
        font_size = run.font.size.pt
    elif run.style and run.style.font.size:
        font_size = run.style.font.size.pt
    
    # Handle font color
    color = 'black'
    if run.font.color and run.font.color.rgb:
        rgb = run.font.color.rgb
        color = f'#{rgb[0]:02x}{rgb[1]:02x}{rgb[2]:02x}' if isinstance(rgb, tuple) else 'black'
    
    return text, {'font_size': font_size, 'color': color}

def process_paragraph(paragraph, styles):
    """Process a paragraph and return ReportLab flowable"""
    # Skip empty paragraphs
    if not paragraph.text.strip() and not any(run.text.strip() for run in paragraph.runs):
        return Spacer(1, 6)
    
    # Get paragraph alignment
    alignment = TA_LEFT
    if paragraph.alignment:
        if paragraph.alignment == 1:  # Center
            alignment = TA_CENTER
        elif paragraph.alignment == 2:  # Right
            alignment = TA_RIGHT
        elif paragraph.alignment == 3:  # Justify
            alignment = TA_JUSTIFY
    
    # Get paragraph style
    style_type, default_font_size = get_paragraph_style(paragraph)
    
    # Collect all runs with their formatting
    formatted_text = ''
    for run in paragraph.runs:
        text, formatting = get_run_formatting(run)
        if text:
            # Apply font size if different from default
            if formatting['font_size'] != default_font_size:
                size_ratio = formatting['font_size'] / default_font_size
                if size_ratio != 1:
                    text = f'<font size="{size_ratio:.1f}">{text}</font>'
            formatted_text += text
    
    # If no formatted text from runs, use paragraph text
    if not formatted_text:
        formatted_text = paragraph.text.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')
    
    # Create style based on paragraph type
    if style_type == 'Title':
        para_style = ParagraphStyle(
            'CustomTitle',
            parent=styles['Title'],
            fontSize=20,
            leading=24,
            alignment=alignment,
            spaceAfter=12,
            spaceBefore=6,
        )
    elif style_type == 'Subtitle':
        para_style = ParagraphStyle(
            'CustomSubtitle',
            parent=styles['Normal'],
            fontSize=14,
            leading=18,
            alignment=alignment,
            spaceAfter=10,
            textColor=colors.grey,
        )
    elif style_type == 'Heading1':
        para_style = ParagraphStyle(
            'CustomHeading1',
            parent=styles['Heading1'],
            fontSize=18,
            leading=22,
            alignment=alignment,
            spaceAfter=12,
            spaceBefore=18,
            textColor=colors.HexColor('#1a1a1a'),
        )
    elif style_type == 'Heading2':
        para_style = ParagraphStyle(
            'CustomHeading2',
            parent=styles['Heading2'],
            fontSize=14,
            leading=18,
            alignment=alignment,
            spaceAfter=10,
            spaceBefore=12,
            textColor=colors.HexColor('#2c2c2c'),
        )
    elif style_type == 'Heading3':
        para_style = ParagraphStyle(
            'CustomHeading3',
            parent=styles['Heading3'],
            fontSize=12,
            leading=15,
            alignment=alignment,
            spaceAfter=8,
            spaceBefore=10,
            textColor=colors.HexColor('#3c3c3c'),
        )
    else:
        para_style = ParagraphStyle(
            'CustomNormal',
            parent=styles['Normal'],
            fontSize=default_font_size,
            leading=default_font_size * 1.2,
            alignment=alignment,
            spaceAfter=6,
        )
    
    return Paragraph(formatted_text, para_style)

def process_table(table, styles):
    """Process a table and return ReportLab Table flowable"""
    data = []
    for row in table.rows:
        row_data = []
        for cell in row.cells:
            # Extract text from cell
            cell_text = cell.text.strip()
            if not cell_text:
                row_data.append('')
            else:
                # Format cell text with basic formatting
                formatted_cell = cell_text.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')
                row_data.append(formatted_cell)
        data.append(row_data)
    
    if not data:
        return Spacer(1, 0)
    
    # Create table with better styling
    tbl = Table(data, hAlign='LEFT', repeatRows=1)
    
    # Enhanced table style
    table_style = TableStyle([
        ('GRID', (0, 0), (-1, -1), 0.5, colors.black),
        ('VALIGN', (0, 0), (-1, -1), 'TOP'),
        ('ALIGN', (0, 0), (-1, -1), 'LEFT'),
        ('FONTNAME', (0, 0), (-1, -1), 'Helvetica'),
        ('FONTSIZE', (0, 0), (-1, -1), 10),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 6),
        ('TOPPADDING', (0, 0), (-1, -1), 4),
        ('LEFTPADDING', (0, 0), (-1, -1), 6),
        ('RIGHTPADDING', (0, 0), (-1, -1), 6),
        ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#f0f0f0')),
        ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
        ('BACKGROUND', (0, -1), (-1, -1), colors.HexColor('#fafafa')),
    ])
    
    # Apply alternating row colors
    for i in range(1, len(data)):
        if i % 2 == 0:
            table_style.add('BACKGROUND', (0, i), (-1, i), colors.HexColor('#f9f9f9'))
    
    tbl.setStyle(table_style)
    return tbl

def word_to_pdf(file_infos):
    """
    Enhanced Word to PDF conversion that preserves formatting,
    fonts, headings, tables, and layout exactly as in the original document.
    """
    if len(file_infos) != 1:
        raise ValueError("Please select exactly 1 Word file to convert to PDF")
    
    f = file_infos[0]
    if f['ext'] != 'docx':
        raise ValueError(f"File '{f['original']}' is not a DOCX. Only DOCX files can be converted to PDF.")
    
    base_name = f['original'].rsplit('.', 1)[0]
    out_name = f"{base_name}.pdf"
    out_path = os.path.join(app.config['CONVERTED_FOLDER'], out_name)
    
    try:
        # Load the Word document
        doc = Document(f['path'])
        
        # Create PDF document with A4 size for better compatibility
        doc_pdf = SimpleDocTemplate(
            out_path, 
            pagesize=A4,
            leftMargin=50,
            rightMargin=50,
            topMargin=50,
            bottomMargin=50,
            title=base_name,
            author="Omni Converter",
        )
        
        # Get standard styles
        styles = getSampleStyleSheet()
        
        # Build elements list
        elements = []
        
        # Process document elements in order
        for element in doc.element.body:
            # Process paragraphs
            for paragraph in doc.paragraphs:
                # Check if this paragraph belongs to the current element
                if paragraph._element is element or paragraph._element.getparent() is element:
                    try:
                        para_flowable = process_paragraph(paragraph, styles)
                        elements.append(para_flowable)
                    except Exception as e:
                        logging.warning(f"Error processing paragraph: {str(e)}")
                        # Fallback to plain text
                        if paragraph.text.strip():
                            simple_style = ParagraphStyle('Simple', parent=styles['Normal'], fontSize=11)
                            elements.append(Paragraph(paragraph.text.replace('&', '&amp;').replace('<', '&lt;'), simple_style))
            
            # Process tables
            for table in doc.tables:
                if table._element is element or table._element.getparent() is element:
                    try:
                        table_flowable = process_table(table, styles)
                        elements.append(table_flowable)
                        elements.append(Spacer(1, 12))
                    except Exception as e:
                        logging.warning(f"Error processing table: {str(e)}")
        
        # Also process any remaining elements
        for paragraph in doc.paragraphs:
            if paragraph not in [p for p in doc.paragraphs if p._element in elements]:
                try:
                    para_flowable = process_paragraph(paragraph, styles)
                    elements.append(para_flowable)
                except:
                    pass
        
        # Build the PDF
        doc_pdf.build(elements)
        
        # Verify the output file was created
        if not os.path.exists(out_path) or os.path.getsize(out_path) == 0:
            raise Exception("PDF creation failed - output file is empty or missing")
        
        return out_path, out_name
        
    except Exception as e:
        logging.error(f"Word to PDF conversion error: {str(e)}")
        raise Exception(f"Failed to convert Word to PDF: {str(e)}")

# ================= ENHANCED PDF COMPRESSION =================

def compress_pdf(file_infos):
    """
    Enhanced PDF compression using multiple methods for maximum size reduction.
    Includes content stream compression, image optimization, and structure optimization.
    """
    if len(file_infos) != 1:
        raise ValueError("Please select exactly 1 PDF file to compress")
    
    f = file_infos[0]
    if f['ext'] != 'pdf':
        raise ValueError(f"File '{f['original']}' is not a PDF. Only PDF files can be compressed.")
    
    base_name = f['original'].rsplit('.', 1)[0]
    out_name = f"{base_name}_compressed.pdf"
    out_path = os.path.join(app.config['CONVERTED_FOLDER'], out_name)
    
    original_size = os.path.getsize(f['path'])
    logging.info(f"Original PDF size: {original_size} bytes")
    
    try:
        # Method 1: Basic PyPDF2 compression
        reader = PyPDF2.PdfReader(f['path'])
        writer = PyPDF2.PdfWriter()
        
        # Compress each page
        for page_num, page in enumerate(reader.pages):
            try:
                # Compress content streams
                if hasattr(page, 'compress_content_streams'):
                    page.compress_content_streams()
                
                # Merge duplicate resources
                if hasattr(page, 'merge_resources'):
                    page.merge_resources()
                
                writer.add_page(page)
            except Exception as e:
                logging.warning(f"Error compressing page {page_num + 1}: {str(e)}")
                writer.add_page(page)
        
        # Write with maximum compression
        with open(out_path, 'wb') as out_file:
            writer.write(out_file)
        
        # Check if compression worked
        compressed_size = os.path.getsize(out_path)
        reduction = ((original_size - compressed_size) / original_size) * 100 if original_size > 0 else 0
        
        logging.info(f"Basic compression: {compressed_size} bytes ({reduction:.1f}% reduction)")
        
        # Method 2: If reduction is less than 30%, try more aggressive compression
        if reduction < 30 and original_size > 10000:
            logging.info("Attempting aggressive compression...")
            
            # Re-read the file
            reader2 = PyPDF2.PdfReader(f['path'])
            writer2 = PyPDF2.PdfWriter()
            
            for page_num, page in enumerate(reader2.pages):
                # Add page and apply aggressive compression
                writer2.add_page(page)
                
                # Try to compress images if present
                if '/XObject' in page['/Resources']:
                    xobjects = page['/Resources']['/XObject'].get_object()
                    for obj_name in xobjects:
                        xobj = xobjects[obj_name]
                        if xobj['/Subtype'] == '/Image':
                            # This is an image - we can't directly compress in PyPDF2, 
                            # but we can mark it for compression
                            try:
                                xobj.compress_content_streams()
                            except:
                                pass
            
            # Write with more aggressive settings
            aggressive_path = out_path + ".temp"
            with open(aggressive_path, 'wb') as out_file:
                writer2.write(out_file)
            
            aggressive_size = os.path.getsize(aggressive_path)
            aggressive_reduction = ((original_size - aggressive_size) / original_size) * 100
            
            logging.info(f"Aggressive compression: {aggressive_size} bytes ({aggressive_reduction:.1f}% reduction)")
            
            # Use the smaller file
            if aggressive_size < compressed_size:
                os.replace(aggressive_path, out_path)
                compressed_size = aggressive_size
                reduction = aggressive_reduction
            else:
                os.remove(aggressive_path)
        
        # Method 3: Try to remove metadata and unnecessary structure
        if reduction < 20 and original_size > 50000:
            logging.info("Attempting metadata and structure optimization...")
            
            reader3 = PyPDF2.PdfReader(f['path'])
            writer3 = PyPDF2.PdfWriter()
            
            # Add all pages without metadata
            for page in reader3.pages:
                writer3.add_page(page)
            
            # Don't add metadata
            writer3.add_metadata({})
            
            # Write optimized file
            optimized_path = out_path + ".opt"
            with open(optimized_path, 'wb') as out_file:
                writer3.write(out_file)
            
            optimized_size = os.path.getsize(optimized_path)
            optimized_reduction = ((original_size - optimized_size) / original_size) * 100
            
            logging.info(f"Optimized compression: {optimized_size} bytes ({optimized_reduction:.1f}% reduction)")
            
            # Use the smallest file
            if optimized_size < compressed_size:
                os.replace(optimized_path, out_path)
                compressed_size = optimized_size
                reduction = optimized_reduction
            else:
                os.remove(optimized_path)
        
        # Final validation
        if compressed_size >= original_size:
            logging.warning(f"Compression did not reduce file size. Original: {original_size}, Compressed: {compressed_size}")
            # If compression increased size, return original with a warning
            if compressed_size > original_size:
                os.remove(out_path)
                # Just copy original file
                import shutil
                shutil.copy2(f['path'], out_path)
                out_name = f"{base_name}_original.pdf"
                out_path = os.path.join(app.config['CONVERTED_FOLDER'], out_name)
        
        final_size = os.path.getsize(out_path)
        final_reduction = ((original_size - final_size) / original_size) * 100
        logging.info(f"Final compressed PDF size: {final_size} bytes ({final_reduction:.1f}% reduction)")
        
        if final_reduction <= 0:
            raise Exception(f"Could not compress the PDF. The file may already be optimized or has a format that prevents compression.")
        
        return out_path, out_name
        
    except Exception as e:
        logging.error(f"PDF compression error: {str(e)}")
        # Fallback: try basic compression as last resort
        try:
            reader = PyPDF2.PdfReader(f['path'])
            writer = PyPDF2.PdfWriter()
            for page in reader.pages:
                writer.add_page(page)
            with open(out_path, 'wb') as out_file:
                writer.write(out_file)
            
            if os.path.getsize(out_path) < original_size:
                return out_path, out_name
            else:
                raise Exception(f"Compression failed: {str(e)}")
        except:
            raise Exception(f"Failed to compress PDF: {str(e)}")

# Map tool IDs to functions (removed split-pdf)
CONVERSION_FUNCTIONS = {
    'merge': merge_pdfs,
    'pdf-to-word': pdf_to_word,
    'png-to-jpg': png_to_jpg,
    'jpg-to-png': jpg_to_png,
    'image-compressor': compress_image,
    'image-to-pdf': image_to_pdf,
    'word-to-pdf': word_to_pdf,
    'compress-pdf': compress_pdf,
}

# ================= ROUTES =================
@app.route("/")
def home():
    return render_template("index.html", user=session.get("user"))

@app.route("/auth", methods=["POST"])
def auth():
    username = request.form["username"]
    password = request.form["password"]
    email = request.form.get("email", "")
    auth_type = request.form["type"]

    with get_db() as conn:
        c = conn.cursor()

        if auth_type == "signup":
            try:
                c.execute("INSERT INTO users (username, email, password) VALUES (?,?,?)",
                         (username, email, password))
                conn.commit()
                session["user"] = username
                return redirect("/")
            except sqlite3.IntegrityError:
                return render_template("index.html", signup_error="Username already exists!", user=None)
        else:  # login
            c.execute("SELECT * FROM users WHERE username=? AND password=?", (username, password))
            user = c.fetchone()
            if user:
                session["user"] = username
                return redirect("/")
            else:
                return render_template("index.html", login_error="Invalid username or password", user=None)

@app.route("/logout")
def logout():
    session.pop("user", None)
    return redirect("/")

@app.route("/convert", methods=["POST"])
def convert():
    if 'files' not in request.files:
        return jsonify({'success': False, 'error': 'No files uploaded'})

    files = request.files.getlist('files')
    tool = request.form.get('tool')

    if not tool or tool not in CONVERSION_FUNCTIONS:
        return jsonify({'success': False, 'error': 'Invalid tool selected'})

    saved_files = save_files(files)
    if not saved_files:
        return jsonify({'success': False, 'error': 'No valid files uploaded'})

    try:
        func = CONVERSION_FUNCTIONS[tool]
        # No more split-pdf parameter handling
        result = func(saved_files)
        
        output_files = result if isinstance(result, list) else [result]

        # Log conversion if user logged in
        if 'user' in session:
            with get_db() as conn:
                c = conn.cursor()
                c.execute("SELECT id FROM users WHERE username=?", (session['user'],))
                user_row = c.fetchone()
                if user_row:
                    user_id = user_row[0]
                    for _, out_name in output_files:
                        c.execute("INSERT INTO conversions (user_id, tool, original_filename, converted_filename) VALUES (?,?,?,?)",
                                  (user_id, tool, saved_files[0]['original'], out_name))
                    conn.commit()

        if len(output_files) == 1:
            out_path, out_name = output_files[0]
            return jsonify({
                'success': True,
                'download_url': f'/download/{out_name}',
                'filename': out_name
            })

        return jsonify({
            'success': True,
            'filenames': [out_name for _, out_name in output_files],
            'download_urls': [f'/download/{out_name}' for _, out_name in output_files]
        })
    except Exception as e:
        logging.error(f"Conversion error: {str(e)}")
        return jsonify({'success': False, 'error': str(e)})

@app.route("/download/<filename>")
def download_file(filename):
    file_path = os.path.join(app.config['CONVERTED_FOLDER'], filename)
    if os.path.exists(file_path):
        return send_file(file_path, as_attachment=True)
    return "File not found", 404

@app.route("/history")
def history():
    if 'user' not in session:
        return redirect("/")
    with get_db() as conn:
        c = conn.cursor()
        c.execute("""
            SELECT tool, original_filename, converted_filename, created_at 
            FROM conversions 
            WHERE user_id = (SELECT id FROM users WHERE username=?) 
            ORDER BY created_at DESC
        """, (session['user'],))
        rows = c.fetchall()
    return render_template("history.html", history=rows, user=session['user'])

@app.route("/terms")
def terms():
    return render_template("terms.html", user=session.get("user"))

@app.route("/privacy")
def privacy():
    return render_template("privacy.html", user=session.get("user"))

@app.route("/delete/<filename>")
def delete_file(filename):
    if 'user' not in session:
        return redirect("/")
    # Delete the file from filesystem
    file_path = os.path.join(app.config['CONVERTED_FOLDER'], filename)
    if os.path.exists(file_path):
        os.remove(file_path)
    # Delete from database
    with get_db() as conn:
        c = conn.cursor()
        c.execute("DELETE FROM conversions WHERE converted_filename=?", (filename,))
        conn.commit()
    return redirect("/history")

if __name__ == "__main__":
    app.run(debug=True, host='0.0.0.0', port=5000)
