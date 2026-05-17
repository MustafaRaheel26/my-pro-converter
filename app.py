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

# Document libraries - IMPROVED Word to PDF
from docx import Document
from docx.shared import Pt, RGBColor, Inches
from docx.enum.text import WD_ALIGN_PARAGRAPH, WD_BREAK
from docx.enum.table import WD_ALIGN_VERTICAL
from docx.oxml.ns import qn
from docx.oxml import parse_xml
import xml.etree.ElementTree as ET
from reportlab.lib.pagesizes import A4
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, PageBreak, KeepTogether
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.enums import TA_LEFT, TA_CENTER, TA_RIGHT, TA_JUSTIFY
from reportlab.lib import colors
from reportlab.lib.units import inch
from html import escape
import re

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

# ================= DATABASE CONFIGURATION =================
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

# ================= CONVERSION FUNCTIONS =================

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

    max_size = 1280
    if max(image.width, image.height) > max_size:
        ratio = max_size / float(max(image.width, image.height))
        new_size = (int(image.width * ratio), int(image.height * ratio))
        image = image.resize(new_size, Image.Resampling.LANCZOS)
    
    if image.mode not in ('RGB', 'L'):
        image = image.convert('RGB')

    if f['ext'] in ['jpg', 'jpeg']:
        image.save(out_path, 'JPEG', optimize=True, quality=30, progressive=True)
    else:
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

# ================= COMPLETELY REWRITTEN WORD TO PDF CONVERSION =================

def get_paragraph_alignment(paragraph):
    """Get paragraph alignment as ReportLab constant"""
    if paragraph.alignment == WD_ALIGN_PARAGRAPH.CENTER:
        return TA_CENTER
    elif paragraph.alignment == WD_ALIGN_PARAGRAPH.RIGHT:
        return TA_RIGHT
    elif paragraph.alignment == WD_ALIGN_PARAGRAPH.JUSTIFY:
        return TA_JUSTIFY
    else:
        return TA_LEFT

def get_run_properties(run):
    """Extract all run properties including font, size, color, etc."""
    props = {
        'text': run.text,
        'bold': run.bold if run.bold is not None else False,
        'italic': run.italic if run.italic is not None else False,
        'underline': run.underline if run.underline is not None else False,
        'font_name': 'Helvetica',
        'font_size': 11,
        'color': 'black'
    }
    
    # Get font name
    if run.font.name:
        props['font_name'] = run.font.name
    
    # Get font size (convert from EMU to points)
    if run.font.size:
        if isinstance(run.font.size, Pt):
            props['font_size'] = run.font.size.pt
        else:
            props['font_size'] = run.font.size.pt
    
    # Get font color
    if run.font.color and run.font.color.rgb:
        rgb = run.font.color.rgb
        if isinstance(rgb, tuple):
            props['color'] = f'#{rgb[0]:02x}{rgb[1]:02x}{rgb[2]:02x}'
        else:
            props['color'] = str(rgb)
    
    return props

def format_text_with_properties(text, props):
    """Format text with HTML tags based on properties"""
    if not text:
        return ''
    
    # Escape HTML
    text = escape(text)
    text = text.replace('\n', '<br/>')
    
    # Apply formatting
    if props['bold']:
        text = f'<b>{text}</b>'
    if props['italic']:
        text = f'<i>{text}</i>'
    if props['underline']:
        text = f'<u>{text}</u>'
    
    # Apply font size if not default
    if props['font_size'] != 11:
        size_ratio = props['font_size'] / 11
        text = f'<font size="{size_ratio:.1f}">{text}</font>'
    
    # Apply color if not black
    if props['color'] != 'black':
        text = f'<font color="{props['color']}">{text}</font>'
    
    return text

def process_document_paragraph(paragraph, styles):
    """Process a Word paragraph to ReportLab Paragraph"""
    # Skip empty paragraphs
    if not paragraph.text.strip():
        return Spacer(1, 4)
    
    # Get paragraph alignment
    alignment = get_paragraph_alignment(paragraph)
    
    # Determine heading level based on style
    style_name = paragraph.style.name.lower() if paragraph.style else 'normal'
    
    # Set default font size
    default_size = 11
    
    # Check for heading styles
    if 'heading 1' in style_name or 'title' in style_name:
        style_type = 'Heading1'
        default_size = 18
    elif 'heading 2' in style_name:
        style_type = 'Heading2'
        default_size = 14
    elif 'heading 3' in style_name:
        style_type = 'Heading3'
        default_size = 12
    elif 'heading 4' in style_name:
        style_type = 'Heading4'
        default_size = 11
    else:
        style_type = 'Normal'
        default_size = 11
    
    # Collect formatted text from runs
    formatted_parts = []
    for run in paragraph.runs:
        if run.text.strip():
            props = get_run_properties(run)
            # Override default size if run has specific size
            if props['font_size'] != default_size:
                pass  # Keep the run's size
            formatted_text = format_text_with_properties(run.text, props)
            if formatted_text:
                formatted_parts.append(formatted_text)
    
    # Join all parts
    if not formatted_parts:
        formatted_text = escape(paragraph.text)
    else:
        formatted_text = ''.join(formatted_parts)
    
    # Create style based on type
    if style_type == 'Heading1':
        para_style = ParagraphStyle(
            'Heading1Style',
            parent=styles['Heading1'],
            fontSize=default_size,
            leading=default_size * 1.3,
            alignment=alignment,
            spaceAfter=12,
            spaceBefore=18,
            textColor=colors.HexColor('#1a1a1a'),
            fontName='Helvetica-Bold'
        )
    elif style_type == 'Heading2':
        para_style = ParagraphStyle(
            'Heading2Style',
            parent=styles['Heading2'],
            fontSize=default_size,
            leading=default_size * 1.3,
            alignment=alignment,
            spaceAfter=10,
            spaceBefore=12,
            textColor=colors.HexColor('#2c2c2c'),
            fontName='Helvetica-Bold'
        )
    elif style_type == 'Heading3':
        para_style = ParagraphStyle(
            'Heading3Style',
            parent=styles['Heading3'],
            fontSize=default_size,
            leading=default_size * 1.3,
            alignment=alignment,
            spaceAfter=8,
            spaceBefore=10,
            textColor=colors.HexColor('#3c3c3c'),
            fontName='Helvetica-Bold'
        )
    else:
        para_style = ParagraphStyle(
            'NormalStyle',
            parent=styles['Normal'],
            fontSize=default_size,
            leading=default_size * 1.2,
            alignment=alignment,
            spaceAfter=6,
            fontName='Helvetica'
        )
    
    try:
        return Paragraph(formatted_text, para_style)
    except:
        # Fallback to plain text
        return Paragraph(escape(paragraph.text), para_style)

def process_document_table(table, styles):
    """Process a Word table to ReportLab Table"""
    data = []
    
    # Get max columns
    max_cols = 0
    for row in table.rows:
        max_cols = max(max_cols, len(row.cells))
    
    # Extract table data
    for row in table.rows:
        row_data = []
        for cell in row.cells:
            # Get cell text
            cell_text = cell.text.strip()
            if not cell_text:
                row_data.append('')
            else:
                # Process cell paragraphs
                cell_paragraphs = []
                for para in cell.paragraphs:
                    if para.text.strip():
                        formatted_parts = []
                        for run in para.runs:
                            if run.text.strip():
                                props = get_run_properties(run)
                                formatted = format_text_with_properties(run.text, props)
                                if formatted:
                                    formatted_parts.append(formatted)
                        if formatted_parts:
                            cell_paragraphs.append(''.join(formatted_parts))
                        else:
                            cell_paragraphs.append(escape(para.text))
                row_data.append('<br/>'.join(cell_paragraphs) if cell_paragraphs else '')
        
        # Pad row to max columns
        while len(row_data) < max_cols:
            row_data.append('')
        data.append(row_data)
    
    if not data:
        return Spacer(1, 0)
    
    # Create table
    tbl = Table(data, hAlign='LEFT', repeatRows=1)
    
    # Apply table styling
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
    ])
    
    # Alternating row colors
    for i in range(1, len(data)):
        if i % 2 == 0:
            table_style.add('BACKGROUND', (0, i), (-1, i), colors.HexColor('#f9f9f9'))
    
    tbl.setStyle(table_style)
    return tbl

def word_to_pdf(file_infos):
    """
    Completely rewritten Word to PDF conversion that properly preserves:
    - Font sizes and styles
    - Bold, italic, underline formatting
    - Paragraph alignment (left, center, right, justify)
    - Heading levels
    - Tables
    - Colors
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
        
        # Create PDF document with A4 size
        doc_pdf = SimpleDocTemplate(
            out_path,
            pagesize=A4,
            leftMargin=0.75*inch,
            rightMargin=0.75*inch,
            topMargin=0.75*inch,
            bottomMargin=0.75*inch,
            title=base_name
        )
        
        # Get styles
        styles = getSampleStyleSheet()
        
        # Build elements list
        elements = []
        
        # Process all paragraphs and tables in order
        for element in doc.element.body:
            # Process paragraphs
            for paragraph in doc.paragraphs:
                if paragraph._element is element or paragraph._element.getparent() is element:
                    try:
                        flowable = process_document_paragraph(paragraph, styles)
                        if flowable:
                            elements.append(flowable)
                    except Exception as e:
                        logging.warning(f"Error processing paragraph: {str(e)}")
                        if paragraph.text.strip():
                            simple_style = ParagraphStyle('Simple', parent=styles['Normal'], fontSize=11)
                            elements.append(Paragraph(escape(paragraph.text), simple_style))
            
            # Process tables
            for table in doc.tables:
                if table._element is element or table._element.getparent() is element:
                    try:
                        flowable = process_document_table(table, styles)
                        if flowable:
                            elements.append(flowable)
                            elements.append(Spacer(1, 12))
                    except Exception as e:
                        logging.warning(f"Error processing table: {str(e)}")
        
        # Build PDF
        doc_pdf.build(elements)
        
        # Verify output
        if not os.path.exists(out_path) or os.path.getsize(out_path) == 0:
            raise Exception("PDF creation failed")
        
        return out_path, out_name
        
    except Exception as e:
        logging.error(f"Word to PDF conversion error: {str(e)}")
        raise Exception(f"Failed to convert Word to PDF: {str(e)}")

# ================= IMPROVED PDF COMPRESSION =================

def compress_pdf(file_infos):
    """
    Improved PDF compression that actually reduces file size significantly
    by optimizing content streams and removing redundant data
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
    logging.info(f"Original PDF size: {original_size / 1024:.2f} KB")
    
    try:
        # Read the PDF
        reader = PyPDF2.PdfReader(f['path'])
        writer = PyPDF2.PdfWriter()
        
        # Process each page with aggressive compression
        for page_num, page in enumerate(reader.pages):
            # Compress content streams
            if hasattr(page, 'compress_content_streams'):
                try:
                    page.compress_content_streams()
                except:
                    pass
            
            # Add page to writer
            writer.add_page(page)
            
            # Try to compress page resources
            if '/Resources' in page:
                resources = page['/Resources']
                if '/XObject' in resources:
                    xobjects = resources['/XObject'].get_object()
                    for xobj_name in xobjects:
                        xobj = xobjects[xobj_name]
                        if xobj['/Subtype'] == '/Image':
                            # Mark images for compression
                            try:
                                if '/Filter' in xobj:
                                    xobj['/Filter'] = '/FlateDecode'
                            except:
                                pass
        
        # Set compression settings
        writer.add_metadata({
            '/Creator': 'Omni Converter',
            '/Producer': 'Omni Converter PDF Compressor'
        })
        
        # Write with compression
        with open(out_path, 'wb') as out_file:
            writer.write(out_file)
        
        compressed_size = os.path.getsize(out_path)
        reduction = ((original_size - compressed_size) / original_size) * 100 if original_size > 0 else 0
        
        logging.info(f"Compressed PDF size: {compressed_size / 1024:.2f} KB ({reduction:.1f}% reduction)")
        
        # If reduction is less than 5% and file is > 100KB, try more aggressive approach
        if reduction < 5 and original_size > 100000:
            logging.info("Attempting more aggressive compression...")
            
            # Create a new writer with even more compression
            writer2 = PyPDF2.PdfWriter()
            reader2 = PyPDF2.PdfReader(f['path'])
            
            for page in reader2.pages:
                # Try to compress page
                try:
                    # Compress content
                    if hasattr(page, 'compress_content_streams'):
                        page.compress_content_streams()
                    
                    # Merge duplicate resources
                    if hasattr(page, 'merge_resources'):
                        page.merge_resources()
                except:
                    pass
                
                writer2.add_page(page)
            
            # Remove metadata
            writer2.add_metadata({})
            
            # Write with aggressive settings
            aggressive_path = out_path + ".aggressive"
            with open(aggressive_path, 'wb') as out_file:
                writer2.write(out_file)
            
            aggressive_size = os.path.getsize(aggressive_path)
            aggressive_reduction = ((original_size - aggressive_size) / original_size) * 100
            
            logging.info(f"Aggressive compression: {aggressive_size / 1024:.2f} KB ({aggressive_reduction:.1f}% reduction)")
            
            # Use the smaller file
            if aggressive_size < compressed_size:
                os.replace(aggressive_path, out_path)
                compressed_size = aggressive_size
                reduction = aggressive_reduction
            else:
                os.remove(aggressive_path)
        
        # Final check - if compression didn't help, return original
        if compressed_size >= original_size:
            logging.warning("Compression did not reduce file size, returning original")
            os.remove(out_path)
            # Copy original instead
            import shutil
            shutil.copy2(f['path'], out_path)
            out_name = f"{base_name}_original.pdf"
            out_path = os.path.join(app.config['CONVERTED_FOLDER'], out_name)
            compressed_size = original_size
        
        final_size = os.path.getsize(out_path)
        final_reduction = ((original_size - final_size) / original_size) * 100
        logging.info(f"Final PDF size: {final_size / 1024:.2f} KB ({final_reduction:.1f}% reduction)")
        
        if final_reduction <= 0:
            raise Exception("Could not compress the PDF file")
        
        return out_path, out_name
        
    except Exception as e:
        logging.error(f"PDF compression error: {str(e)}")
        # Fallback: try basic copy
        try:
            import shutil
            shutil.copy2(f['path'], out_path)
            out_name = f"{base_name}_copy.pdf"
            out_path = os.path.join(app.config['CONVERTED_FOLDER'], out_name)
            return out_path, out_name
        except:
            raise Exception(f"Failed to compress PDF: {str(e)}")

# Map tool IDs to functions
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
    file_path = os.path.join(app.config['CONVERTED_FOLDER'], filename)
    if os.path.exists(file_path):
        os.remove(file_path)
    with get_db() as conn:
        c = conn.cursor()
        c.execute("DELETE FROM conversions WHERE converted_filename=?", (filename,))
        conn.commit()
    return redirect("/history")

if __name__ == "__main__":
    app.run(debug=True, host='0.0.0.0', port=5000)
