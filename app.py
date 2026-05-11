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

# Document libraries
from docx import Document
from reportlab.pdfgen import canvas
from reportlab.lib.pagesizes import letter
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table
from reportlab.platypus.tables import TableStyle
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.enums import TA_LEFT, TA_CENTER, TA_RIGHT, TA_JUSTIFY
from reportlab.lib import colors

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

# ================= CONVERSION FUNCTIONS =================
def merge_pdfs(file_infos):
    """
    Merge multiple PDF files into one.
    Requires exactly 2 PDF files.
    """
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

def word_to_pdf(file_infos):
    if len(file_infos) != 1:
        raise ValueError("Please select exactly 1 Word file to convert to PDF")
    
    f = file_infos[0]
    if f['ext'] != 'docx':
        raise ValueError(f"File '{f['original']}' is not a DOCX. Only DOCX files can be converted to PDF.")

    base_name = f['original'].rsplit('.', 1)[0]
    out_name = f"{base_name}.pdf"
    out_path = os.path.join(app.config['CONVERTED_FOLDER'], out_name)
    doc = Document(f['path'])

    def escape(text):
        return (
            text.replace('&', '&amp;')
            .replace('<', '&lt;')
            .replace('>', '&gt;')
            .replace('\n', '<br/>')
        )

    def get_font_size(run):
        """Extract font size from run or use default"""
        if run.font.size:
            return run.font.size.pt
        return 11

    def format_run(run):
        text = escape(run.text)
        font_size = get_font_size(run)
        
        # Apply text formatting
        if run.bold:
            text = f"<b>{text}</b>"
        if run.italic:
            text = f"<i>{text}</i>"
        if run.underline:
            text = f"<u>{text}</u>"
        
        # Apply font size if significantly different
        if font_size != 11:
            text = f"<font size={int(font_size/11)}>{text}</font>"
        
        return text

    styles = getSampleStyleSheet()
    
    # Define better styles that preserve more formatting
    body_style = ParagraphStyle(
        name='BodyStyle',
        parent=styles['BodyText'],
        fontName='Courier',
        fontSize=11,
        leading=14,
        alignment=TA_LEFT,
        spaceAfter=8,
    )
    heading1_style = ParagraphStyle(
        name='Heading1Style',
        parent=styles['Heading1'],
        fontName='Courier-Bold',
        fontSize=18,
        leading=22,
        spaceAfter=12,
    )
    heading2_style = ParagraphStyle(
        name='Heading2Style',
        parent=styles['Heading2'],
        fontName='Courier-Bold',
        fontSize=14,
        leading=18,
        spaceAfter=10,
    )
    heading3_style = ParagraphStyle(
        name='Heading3Style',
        parent=styles['Heading3'],
        fontName='Courier-Bold',
        fontSize=12,
        leading=15,
        spaceAfter=8,
    )

    elements = []
    for paragraph in doc.paragraphs:
        paragraph_text = ''.join(format_run(run) for run in paragraph.runs)
        if not paragraph_text.strip():
            elements.append(Spacer(1, 8))
            continue
        
        # Determine style based on paragraph style name
        style_name = paragraph.style.name.lower()
        if 'heading 1' in style_name:
            style = heading1_style
        elif 'heading 2' in style_name:
            style = heading2_style
        elif 'heading 3' in style_name:
            style = heading3_style
        else:
            style = body_style
        
        elements.append(Paragraph(paragraph_text, style))

    for table in doc.tables:
        table_data = [[cell.text.strip() for cell in row.cells] for row in table.rows]
        table_style = TableStyle([
            ('GRID', (0, 0), (-1, -1), 1, colors.black),
            ('VALIGN', (0, 0), (-1, -1), 'TOP'),
            ('ALIGN', (0, 0), (-1, -1), 'LEFT'),
            ('FONTNAME', (0, 0), (-1, -1), 'Courier'),
            ('FONTSIZE', (0, 0), (-1, -1), 9),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 4),
            ('BACKGROUND', (0, 0), (-1, 0), colors.lightgrey),
        ])
        tbl = Table(table_data, hAlign='LEFT')
        tbl.setStyle(table_style)
        elements.append(tbl)
        elements.append(Spacer(1, 12))

    doc_pdf = SimpleDocTemplate(out_path, pagesize=letter, leftMargin=40, rightMargin=40, topMargin=40, bottomMargin=40)
    doc_pdf.build(elements)
    return out_path, out_name

def split_pdf(file_infos, start_page=1, end_page=None):
    if len(file_infos) != 1:
        raise ValueError("Please select exactly 1 PDF file to split")
    
    f = file_infos[0]
    if f['ext'] != 'pdf':
        raise ValueError(f"File '{f['original']}' is not a PDF. Only PDF files can be split.")

    reader = PyPDF2.PdfReader(f['path'])
    total_pages = len(reader.pages)
    if total_pages == 0:
        raise ValueError("PDF file is empty or invalid.")

    if start_page is None:
        start_page = 1
    if end_page is None:
        end_page = total_pages

    if start_page < 1 or end_page < start_page or end_page > total_pages:
        raise ValueError(f"Invalid page range. Use values between 1 and {total_pages}.")

    base_name = f['original'].rsplit('.', 1)[0]
    outputs = []
    for page_index in range(start_page - 1, end_page):
        writer = PyPDF2.PdfWriter()
        writer.add_page(reader.pages[page_index])
        page_number = page_index + 1
        out_name = f"{base_name}_page_{page_number}.pdf"
        out_path = os.path.join(app.config['CONVERTED_FOLDER'], out_name)
        with open(out_path, 'wb') as out_file:
            writer.write(out_file)
        outputs.append((out_path, out_name))

    return outputs

def compress_pdf(file_infos):
    if len(file_infos) != 1:
        raise ValueError("Please select exactly 1 PDF file to compress")
    
    f = file_infos[0]
    if f['ext'] != 'pdf':
        raise ValueError(f"File '{f['original']}' is not a PDF. Only PDF files can be compressed.")
    
    base_name = f['original'].rsplit('.', 1)[0]
    out_name = f"{base_name}_compressed.pdf"
    out_path = os.path.join(app.config['CONVERTED_FOLDER'], out_name)
    
    reader = PyPDF2.PdfReader(f['path'])
    writer = PyPDF2.PdfWriter()
    
    for page in reader.pages:
        try:
            # Compress content streams
            page.compress_content_streams()
            
            # Remove duplication in content streams
            if "/Contents" in page:
                page["/Contents"].get_object().decodedSelf
        except Exception:
            pass
        writer.add_page(page)
    
    # Write with additional compression
    with open(out_path, 'wb') as out_file:
        writer.write(out_file)
    
    return out_path, out_name

# Map tool IDs to functions
CONVERSION_FUNCTIONS = {
    'merge': merge_pdfs,
    'pdf-to-word': pdf_to_word,
    'png-to-jpg': png_to_jpg,
    'jpg-to-png': jpg_to_png,
    'image-compressor': compress_image,
    'image-to-pdf': image_to_pdf,
    'word-to-pdf': word_to_pdf,
    'split-pdf': split_pdf,
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
        if tool == 'split-pdf':
            start_page = request.form.get('start_page')
            end_page = request.form.get('end_page')
            result = func(
                saved_files,
                start_page=int(start_page) if start_page else None,
                end_page=int(end_page) if end_page else None,
            )
        else:
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