from flask import Flask, render_template, request, redirect, session, jsonify, send_file
import sqlite3
import os
import uuid
from werkzeug.utils import secure_filename
import threading
import time
import logging
import tempfile
from contextlib import contextmanager

# PDF libraries
import PyPDF2
from pdf2docx import Converter
import pdfplumber

# Image libraries
from PIL import Image
import img2pdf

# Document libraries
import pandas as pd
from docx import Document
from reportlab.pdfgen import canvas
from reportlab.lib.pagesizes import letter
from pptx import Presentation
from pptx.util import Inches

# PDF to PPT image conversion
from pdf2image import convert_from_path

app = Flask(__name__)
app.secret_key = "supersecretkeyproconverter2026"
app.config['MAX_CONTENT_LENGTH'] = 50 * 1024 * 1024  # 50MB
app.config['UPLOAD_FOLDER'] = 'uploads'
app.config['CONVERTED_FOLDER'] = 'converted'

# Create folders
for folder in [app.config['UPLOAD_FOLDER'], app.config['CONVERTED_FOLDER']]:
    os.makedirs(folder, exist_ok=True)

logging.basicConfig(level=logging.INFO)

# Allowed extensions
ALLOWED_EXTENSIONS = {'pdf', 'png', 'jpg', 'jpeg', 'docx', 'xlsx', 'pptx', 'txt'}

# ================= DATABASE FIX FOR RENDER (Persistent Storage) =================
# This fixes the database issue on Render's free tier
if os.environ.get('RENDER'):
    # On Render cloud - use persistent storage
    DATABASE = '/var/data/users.db'
    # Ensure the directory exists
    os.makedirs('/var/data', exist_ok=True)
else:
    # On local computer
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
    Requires at least 2 valid PDF files.
    """
    if len(file_infos) < 2:
        raise ValueError("Please select at least 2 PDF files to merge")

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

    out_name = f"merged_{uuid.uuid4().hex}.pdf"
    out_path = os.path.join(app.config['CONVERTED_FOLDER'], out_name)

    try:
        merger.write(out_path)
        merger.close()
        return out_path, out_name
    except Exception as e:
        merger.close()
        raise Exception(f"Failed to write merged PDF: {str(e)}")

def pdf_to_word(file_infos):
    f = file_infos[0]
    out_name = f"{uuid.uuid4().hex}.docx"
    out_path = os.path.join(app.config['CONVERTED_FOLDER'], out_name)
    cv = Converter(f['path'])
    cv.convert(out_path, start=0, end=None)
    cv.close()
    return out_path, out_name

def pdf_to_excel(file_infos):
    f = file_infos[0]
    out_name = f"{uuid.uuid4().hex}.xlsx"
    out_path = os.path.join(app.config['CONVERTED_FOLDER'], out_name)

    with pdfplumber.open(f['path']) as pdf:
        all_tables = []
        for page in pdf.pages:
            tables = page.extract_tables()
            for table in tables:
                if table:
                    df = pd.DataFrame(table[1:], columns=table[0] if table[0] else None)
                    all_tables.append(df)

    if all_tables:
        with pd.ExcelWriter(out_path, engine='openpyxl') as writer:
            for i, df in enumerate(all_tables):
                df.to_excel(writer, sheet_name=f'Table_{i+1}', index=False)
    else:
        text = []
        with pdfplumber.open(f['path']) as pdf:
            for page in pdf.pages:
                text.append([page.extract_text()])
        df = pd.DataFrame(text, columns=['Content'])
        df.to_excel(out_path, index=False)
    return out_path, out_name

def pdf_to_ppt(file_infos):
    f = file_infos[0]
    out_name = f"{uuid.uuid4().hex}.pptx"
    out_path = os.path.join(app.config['CONVERTED_FOLDER'], out_name)

    try:
        # Try to use pdf2image if poppler is available (will work on Render after we install it)
        images = convert_from_path(f['path'], dpi=150)
        prs = Presentation()

        for img in images:
            with tempfile.NamedTemporaryFile(suffix='.png', delete=False) as tmp:
                temp_path = tmp.name
                img.save(temp_path, 'PNG')

            slide_layout = prs.slide_layouts[6]
            slide = prs.slides.add_slide(slide_layout)
            left = top = Inches(0.5)
            slide.shapes.add_picture(temp_path, left, top, height=Inches(6))
            os.unlink(temp_path)

        prs.save(out_path)
        return out_path, out_name

    except Exception as e:
        logging.error(f"PDF to PPT error (fallback to text): {str(e)}")
        # Fallback: simple text-based PPT (always works)
        prs = Presentation()
        reader = PyPDF2.PdfReader(f['path'])
        for page_num, page in enumerate(reader.pages):
            text = page.extract_text()
            if text.strip():
                slide_layout = prs.slide_layouts[1]
                slide = prs.slides.add_slide(slide_layout)
                slide.shapes.title.text = f"Page {page_num + 1}"
                slide.placeholders[1].text = text[:5000]
        prs.save(out_path)
        return out_path, out_name

def png_to_jpg(file_infos):
    f = file_infos[0]
    out_name = f"{uuid.uuid4().hex}.jpg"
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
    f = file_infos[0]
    out_name = f"{uuid.uuid4().hex}.png"
    out_path = os.path.join(app.config['CONVERTED_FOLDER'], out_name)
    Image.open(f['path']).save(out_path, 'PNG')
    return out_path, out_name

def compress_image(file_infos):
    f = file_infos[0]
    out_name = f"compressed_{uuid.uuid4().hex}.{f['ext']}"
    out_path = os.path.join(app.config['CONVERTED_FOLDER'], out_name)
    image = Image.open(f['path'])
    image.save(out_path, optimize=True, quality=50)
    return out_path, out_name

def image_to_pdf(file_infos):
    images = []
    for f in file_infos:
        img = Image.open(f['path']).convert('RGB')
        images.append(img)
    out_name = f"{uuid.uuid4().hex}.pdf"
    out_path = os.path.join(app.config['CONVERTED_FOLDER'], out_name)
    if images:
        if len(images) == 1:
            images[0].save(out_path, 'PDF', resolution=100.0)
        else:
            images[0].save(out_path, 'PDF', resolution=100.0, save_all=True, append_images=images[1:])
    return out_path, out_name

def word_to_pdf(file_infos):
    f = file_infos[0]
    out_name = f"{uuid.uuid4().hex}.pdf"
    out_path = os.path.join(app.config['CONVERTED_FOLDER'], out_name)
    doc = Document(f['path'])
    c = canvas.Canvas(out_path, pagesize=letter)
    width, height = letter
    y = height - 50
    for para in doc.paragraphs:
        text = para.text.strip()
        if not text:
            continue
        lines = []
        words = text.split()
        line = ""
        for word in words:
            if c.stringWidth(line + " " + word) < width - 100:
                line += " " + word if line else word
            else:
                lines.append(line)
                line = word
        if line:
            lines.append(line)
        for line in lines:
            if y < 50:
                c.showPage()
                y = height - 50
            c.drawString(50, y, line)
            y -= 20
    c.save()
    return out_path, out_name

def background_remover(file_infos):
    """
    Simple background remover using PIL (no AI, just makes white-ish pixels transparent).
    """
    f = file_infos[0]
    out_name = f"nobg_{uuid.uuid4().hex}.png"
    out_path = os.path.join(app.config['CONVERTED_FOLDER'], out_name)

    image = Image.open(f['path']).convert('RGBA')
    data = image.getdata()

    new_data = []
    for item in data:
        if item[0] > 200 and item[1] > 200 and item[2] > 200:
            new_data.append((255, 255, 255, 0))
        else:
            new_data.append(item)

    image.putdata(new_data)
    image.save(out_path, 'PNG')
    return out_path, out_name

def split_pdf(file_infos):
    f = file_infos[0]
    out_name = f"split_{uuid.uuid4().hex}.pdf"
    out_path = os.path.join(app.config['CONVERTED_FOLDER'], out_name)
    reader = PyPDF2.PdfReader(f['path'])
    writer = PyPDF2.PdfWriter()
    if len(reader.pages) > 0:
        writer.add_page(reader.pages[0])
        with open(out_path, 'wb') as out_file:
            writer.write(out_file)
    return out_path, out_name

def compress_pdf(file_infos):
    f = file_infos[0]
    out_name = f"compressed_{uuid.uuid4().hex}.pdf"
    out_path = os.path.join(app.config['CONVERTED_FOLDER'], out_name)
    reader = PyPDF2.PdfReader(f['path'])
    writer = PyPDF2.PdfWriter()
    for page in reader.pages:
        writer.add_page(page)
    writer.add_metadata({})
    with open(out_path, 'wb') as out_file:
        writer.write(out_file)
    return out_path, out_name

def excel_to_pdf(file_infos):
    f = file_infos[0]
    out_name = f"{uuid.uuid4().hex}.pdf"
    out_path = os.path.join(app.config['CONVERTED_FOLDER'], out_name)
    df_dict = pd.read_excel(f['path'], sheet_name=None)
    c = canvas.Canvas(out_path, pagesize=letter)
    width, height = letter
    y = height - 50
    for sheet_name, sheet_df in df_dict.items():
        c.setFont("Helvetica-Bold", 14)
        c.drawString(50, y, f"Sheet: {sheet_name}")
        y -= 30
        for index, row in sheet_df.iterrows():
            row_str = ' | '.join([str(val) for val in row.values])
            if y < 50:
                c.showPage()
                y = height - 50
            c.setFont("Helvetica", 10)
            c.drawString(50, y, row_str[:100])
            y -= 15
        y -= 20
    c.save()
    return out_path, out_name

def ppt_to_pdf(file_infos):
    f = file_infos[0]
    out_name = f"{uuid.uuid4().hex}.pdf"
    out_path = os.path.join(app.config['CONVERTED_FOLDER'], out_name)
    prs = Presentation(f['path'])
    c = canvas.Canvas(out_path, pagesize=letter)
    width, height = letter
    for slide in prs.slides:
        y = height - 50
        for shape in slide.shapes:
            if hasattr(shape, "text"):
                text = shape.text.strip()
                if text:
                    if y < 50:
                        c.showPage()
                        y = height - 50
                    c.drawString(50, y, text[:100])
                    y -= 20
        c.showPage()
    c.save()
    return out_path, out_name

def resize_image(file_infos):
    f = file_infos[0]
    out_name = f"resized_{uuid.uuid4().hex}.{f['ext']}"
    out_path = os.path.join(app.config['CONVERTED_FOLDER'], out_name)
    image = Image.open(f['path'])
    new_size = (int(image.width * 0.5), int(image.height * 0.5))
    resized = image.resize(new_size, Image.Resampling.LANCZOS)
    resized.save(out_path)
    return out_path, out_name

# Map tool IDs to functions
CONVERSION_FUNCTIONS = {
    'merge': merge_pdfs,
    'pdf-to-word': pdf_to_word,
    'pdf-to-excel': pdf_to_excel,
    'pdf-to-ppt': pdf_to_ppt,
    'png-to-jpg': png_to_jpg,
    'jpg-to-png': jpg_to_png,
    'image-compressor': compress_image,
    'image-to-pdf': image_to_pdf,
    'word-to-pdf': word_to_pdf,
    'background-remover': background_remover,
    'split-pdf': split_pdf,
    'compress-pdf': compress_pdf,
    'excel-to-pdf': excel_to_pdf,
    'ppt-to-pdf': ppt_to_pdf,
    'resize-image': resize_image,
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
        out_path, out_name = func(saved_files)

        # Log conversion if user logged in
        if 'user' in session:
            with get_db() as conn:
                c = conn.cursor()
                c.execute("SELECT id FROM users WHERE username=?", (session['user'],))
                user_row = c.fetchone()
                if user_row:
                    user_id = user_row[0]
                    c.execute("INSERT INTO conversions (user_id, tool, original_filename, converted_filename) VALUES (?,?,?,?)",
                             (user_id, tool, saved_files[0]['original'], out_name))
                    conn.commit()

        return jsonify({
            'success': True,
            'download_url': f'/download/{out_name}',
            'filename': out_name
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

# ================= ADDED HISTORY AND DELETE ROUTES (for completeness) =================
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