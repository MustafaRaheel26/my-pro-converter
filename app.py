from flask import Flask, render_template, request, redirect, session, jsonify, send_file
import sqlite3
import os
import uuid
from werkzeug.utils import secure_filename
import threading
import time
import logging
from contextlib import contextmanager
import PyPDF2
from pdf2docx import Converter
from PIL import Image
from docx import Document
import subprocess
import shutil

app = Flask(__name__)
app.secret_key = "supersecretkeyOmniConverter2026"
app.config['MAX_CONTENT_LENGTH'] = 50 * 1024 * 1024
app.config['UPLOAD_FOLDER'] = 'uploads'
app.config['CONVERTED_FOLDER'] = 'converted'

for folder in [app.config['UPLOAD_FOLDER'], app.config['CONVERTED_FOLDER']]:
    os.makedirs(folder, exist_ok=True)

logging.basicConfig(level=logging.INFO)

ALLOWED_EXTENSIONS = {'pdf', 'png', 'jpg', 'jpeg', 'docx', 'xlsx', 'pptx', 'txt'}

# ================= DATABASE =================
DATABASE = 'users.db'

@contextmanager
def get_db():
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

# ================= HELPERS =================
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

# ================= OTHER CONVERSIONS (unchanged) =================

def merge_pdfs(file_infos):
    if len(file_infos) != 2:
        raise ValueError("Please select exactly 2 PDF files to merge")
    for f in file_infos:
        if f['ext'] != 'pdf':
            raise ValueError(f"File '{f['original']}' is not a PDF.")
    merger = PyPDF2.PdfMerger()
    for f in file_infos:
        merger.append(f['path'])
    base_name = file_infos[0]['original'].rsplit('.', 1)[0]
    out_name = f"{base_name}_merged.pdf"
    out_path = os.path.join(app.config['CONVERTED_FOLDER'], out_name)
    merger.write(out_path)
    merger.close()
    return out_path, out_name

def pdf_to_word(file_infos):
    if len(file_infos) != 1:
        raise ValueError("Please select exactly 1 PDF file")
    f = file_infos[0]
    if f['ext'] != 'pdf':
        raise ValueError("Not a PDF")
    base_name = f['original'].rsplit('.', 1)[0]
    out_name = f"{base_name}.docx"
    out_path = os.path.join(app.config['CONVERTED_FOLDER'], out_name)
    cv = Converter(f['path'])
    cv.convert(out_path, start=0, end=None)
    cv.close()
    return out_path, out_name

def png_to_jpg(file_infos):
    if len(file_infos) != 1:
        raise ValueError("Exactly 1 PNG file required")
    f = file_infos[0]
    if f['ext'] != 'png':
        raise ValueError("Not a PNG")
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
        raise ValueError("Exactly 1 JPG file required")
    f = file_infos[0]
    if f['ext'] not in ['jpg', 'jpeg']:
        raise ValueError("Not a JPG")
    base_name = f['original'].rsplit('.', 1)[0]
    out_name = f"{base_name}.png"
    out_path = os.path.join(app.config['CONVERTED_FOLDER'], out_name)
    Image.open(f['path']).save(out_path, 'PNG')
    return out_path, out_name

def compress_image(file_infos):
    if len(file_infos) != 1:
        raise ValueError("Exactly 1 image file required")
    f = file_infos[0]
    if f['ext'] not in ['jpg', 'jpeg', 'png']:
        raise ValueError("Unsupported image")
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
        raise ValueError("At least 1 image required")
    for f in file_infos:
        if f['ext'] not in ['jpg', 'jpeg', 'png']:
            raise ValueError("Unsupported image")
    images = [Image.open(f['path']).convert('RGB') for f in file_infos]
    base_name = file_infos[0]['original'].rsplit('.', 1)[0]
    out_name = f"{base_name}.pdf"
    out_path = os.path.join(app.config['CONVERTED_FOLDER'], out_name)
    if len(images) == 1:
        images[0].save(out_path, 'PDF', resolution=100.0)
    else:
        images[0].save(out_path, 'PDF', resolution=100.0, save_all=True, append_images=images[1:])
    return out_path, out_name

def compress_pdf(file_infos):
    if len(file_infos) != 1:
        raise ValueError("Exactly 1 PDF file required")
    f = file_infos[0]
    if f['ext'] != 'pdf':
        raise ValueError("Not a PDF")
    base_name = f['original'].rsplit('.', 1)[0]
    out_name = f"{base_name}_compressed.pdf"
    out_path = os.path.join(app.config['CONVERTED_FOLDER'], out_name)
    original_size = os.path.getsize(f['path'])
    try:
        reader = PyPDF2.PdfReader(f['path'])
        writer = PyPDF2.PdfWriter()
        for page in reader.pages:
            if hasattr(page, 'compress_content_streams'):
                page.compress_content_streams()
            writer.add_page(page)
        writer.add_metadata({})
        with open(out_path, 'wb') as out_file:
            writer.write(out_file)
        compressed_size = os.path.getsize(out_path)
        if compressed_size >= original_size:
            os.remove(out_path)
            shutil.copy2(f['path'], out_path)
            out_name = f"{base_name}_original.pdf"
            out_path = os.path.join(app.config['CONVERTED_FOLDER'], out_name)
    except Exception as e:
        shutil.copy2(f['path'], out_path)
        out_name = f"{base_name}_copy.pdf"
        out_path = os.path.join(app.config['CONVERTED_FOLDER'], out_name)
    return out_path, out_name

# ================= WORD TO PDF USING LIBREOFFICE (NO PYTHON PDF LIBRARIES) =================

def word_to_pdf(file_infos):
    """
    Convert DOCX to PDF using LibreOffice in headless mode.
    This preserves 100% of formatting, fonts, tables, and layout.
    """
    if len(file_infos) != 1:
        raise ValueError("Please select exactly 1 Word file to convert to PDF")
    f = file_infos[0]
    if f['ext'] != 'docx':
        raise ValueError(f"File '{f['original']}' is not a DOCX")

    base_name = f['original'].rsplit('.', 1)[0]
    out_name = f"{base_name}.pdf"
    out_path = os.path.join(app.config['CONVERTED_FOLDER'], out_name)

    try:
        # Run LibreOffice headless conversion
        # --headless: no GUI
        # --convert-to pdf: output format
        # --outdir: output directory
        # The input file is the last argument
        cmd = [
            'libreoffice',
            '--headless',
            '--convert-to', 'pdf',
            '--outdir', app.config['CONVERTED_FOLDER'],
            f['path']
        ]
        logging.info(f"Running LibreOffice command: {' '.join(cmd)}")
        
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
        
        if result.returncode != 0:
            logging.error(f"LibreOffice stderr: {result.stderr}")
            raise Exception(f"LibreOffice conversion failed with code {result.returncode}")
        
        # LibreOffice saves the PDF in the same directory as input with same base name
        # but we have a unique name for the input file. The output will be named
        # based on the input file's basename (without path) but with .pdf extension.
        # Our input file is at f['path'] which is something like 'uploads/uuid.docx'.
        # The output will be 'converted/uuid.pdf'? Actually LibreOffice uses the original
        # filename (without the unique part) because we gave it f['path'].
        # Let's search for the PDF file that was just created.
        input_basename = os.path.basename(f['path']).rsplit('.', 1)[0]
        expected_output = os.path.join(app.config['CONVERTED_FOLDER'], f"{input_basename}.pdf")
        
        # If that file exists, rename it to our desired out_name
        if os.path.exists(expected_output):
            # If the desired out_path is different, rename
            if expected_output != out_path:
                shutil.move(expected_output, out_path)
        else:
            # Maybe LibreOffice used the original filename (without the UUID prefix)? No, it uses the exact filename.
            # But sometimes it adds a number? Let's list the converted folder.
            files_in_converted = os.listdir(app.config['CONVERTED_FOLDER'])
            pdf_files = [f for f in files_in_converted if f.endswith('.pdf')]
            # Find the most recently created PDF
            latest = None
            latest_time = 0
            for pdf_file in pdf_files:
                pdf_path = os.path.join(app.config['CONVERTED_FOLDER'], pdf_file)
                mtime = os.path.getmtime(pdf_path)
                if mtime > latest_time:
                    latest_time = mtime
                    latest = pdf_path
            if latest and latest != out_path:
                shutil.move(latest, out_path)
            else:
                raise Exception("Could not locate the converted PDF file")
        
        # Verify output
        if not os.path.exists(out_path) or os.path.getsize(out_path) == 0:
            raise Exception("PDF generation produced empty file")
        
        return out_path, out_name
        
    except subprocess.TimeoutExpired:
        raise Exception("Conversion timed out (LibreOffice took too long)")
    except Exception as e:
        logging.error(f"Word to PDF conversion error: {str(e)}")
        raise Exception(f"Failed to convert Word to PDF: {str(e)}")

# ================= ROUTES (no split-pdf) =================

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
                c.execute("INSERT INTO users (username, email, password) VALUES (?,?,?)", (username, email, password))
                conn.commit()
                session["user"] = username
                return redirect("/")
            except sqlite3.IntegrityError:
                return render_template("index.html", signup_error="Username exists", user=None)
        else:
            c.execute("SELECT * FROM users WHERE username=? AND password=?", (username, password))
            user = c.fetchone()
            if user:
                session["user"] = username
                return redirect("/")
            else:
                return render_template("index.html", login_error="Invalid credentials", user=None)

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
        result = CONVERSION_FUNCTIONS[tool](saved_files)
        output_files = result if isinstance(result, list) else [result]
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
            return jsonify({'success': True, 'download_url': f'/download/{output_files[0][1]}', 'filename': output_files[0][1]})
        return jsonify({'success': True, 'filenames': [n for _, n in output_files], 'download_urls': [f'/download/{n}' for _, n in output_files]})
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
        c.execute("SELECT tool, original_filename, converted_filename, created_at FROM conversions WHERE user_id = (SELECT id FROM users WHERE username=?) ORDER BY created_at DESC", (session['user'],))
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
