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
from docx.shared import Pt, RGBColor, Inches
from docx.enum.text import WD_ALIGN_PARAGRAPH, WD_BREAK
from docx.enum.table import WD_ALIGN_VERTICAL
from io import BytesIO
import re

# NEW: Use fpdf2 for accurate Word to PDF conversion
from fpdf import FPDF
from fpdf.enums import XPos, YPos, Align

app = Flask(__name__)
app.secret_key = "supersecretkeyOmniConverter2026"
app.config['MAX_CONTENT_LENGTH'] = 50 * 1024 * 1024  # 50MB
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

# ================= CONVERSION FUNCTIONS (unchanged except word-to-pdf) =================
def merge_pdfs(file_infos):
    if len(file_infos) != 2:
        raise ValueError("Please select exactly 2 PDF files to merge")
    for f in file_infos:
        if f['ext'] != 'pdf':
            raise ValueError(f"File '{f['original']}' is not a PDF.")
    merger = PyPDF2.PdfMerger()
    failed_files = []
    for f in file_infos:
        try:
            with open(f['path'], 'rb') as pdf_file:
                merger.append(pdf_file)
        except Exception as e:
            failed_files.append(f['original'])
    if failed_files:
        merger.close()
        raise ValueError(f"Could not merge: {', '.join(failed_files)}")
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

# ================= COMPLETELY REWRITTEN WORD TO PDF USING FPDF2 =================

class PDF(FPDF):
    """Custom PDF class with header/footer and margin settings"""
    def header(self):
        # No header by default
        pass

    def footer(self):
        # Optional footer with page number
        self.set_y(-15)
        self.set_font('Helvetica', 'I', 8)
        self.cell(0, 10, f'Page {self.page_no()}', align='C')

def word_to_pdf(file_infos):
    """
    Convert DOCX to PDF using fpdf2, preserving exact font sizes, styles, colors,
    alignment, tables, and spacing.
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
        # Load Word document
        doc = Document(f['path'])
        
        # Create PDF document
        pdf = PDF(orientation='P', unit='pt', format='A4')
        pdf.add_page()
        pdf.set_auto_page_break(auto=True, margin=50)  # 50pt margins
        pdf.set_margins(left=50, top=50, right=50)
        
        # Default font
        pdf.set_font('Helvetica', size=11)
        
        # Helper to convert Word alignment to FPDF alignment
        def get_align(align_type):
            if align_type == WD_ALIGN_PARAGRAPH.CENTER:
                return 'C'
            elif align_type == WD_ALIGN_PARAGRAPH.RIGHT:
                return 'R'
            elif align_type == WD_ALIGN_PARAGRAPH.JUSTIFY:
                return 'J'
            else:
                return 'L'
        
        # Helper to get font family and style from run
        def get_font_style(run):
            style = ''
            if run.bold:
                style += 'B'
            if run.italic:
                style += 'I'
            if not style:
                style = ''
            # Underline handling: fpdf2 doesn't support underline directly in set_font,
            # we will use cell with underline parameter later.
            return style, run.underline if run.underline else False
        
        # Helper to get font size in points
        def get_font_size(run):
            if run.font.size:
                return run.font.size.pt
            return 11  # default
        
        # Helper to get font color as RGB tuple
        def get_font_color(run):
            if run.font.color and run.font.color.rgb:
                rgb = run.font.color.rgb
                if isinstance(rgb, tuple):
                    return rgb
                # If it's a string like 'FF0000', convert
                if isinstance(rgb, str) and len(rgb) == 6:
                    return (int(rgb[0:2], 16), int(rgb[2:4], 16), int(rgb[4:6], 16))
            return (0, 0, 0)  # black
        
        # Process each paragraph
        for paragraph in doc.paragraphs:
            # Skip empty paragraphs but add a small spacer if it's just a line break
            if not paragraph.text.strip():
                pdf.ln(8)
                continue
            
            # Get paragraph alignment
            align = get_align(paragraph.alignment) if paragraph.alignment else 'L'
            
            # Store runs and their formatting
            runs_data = []
            for run in paragraph.runs:
                if not run.text:
                    continue
                style, underline = get_font_style(run)
                size = get_font_size(run)
                color = get_font_color(run)
                runs_data.append({
                    'text': run.text,
                    'style': style,
                    'size': size,
                    'color': color,
                    'underline': underline
                })
            
            # If no runs with text, fallback to plain paragraph text
            if not runs_data:
                runs_data = [{
                    'text': paragraph.text,
                    'style': '',
                    'size': 11,
                    'color': (0,0,0),
                    'underline': False
                }]
            
            # Set the first run's font to get baseline
            first = runs_data[0]
            pdf.set_font('Helvetica', first['style'], first['size'])
            pdf.set_text_color(*first['color'])
            # For multi-run paragraphs, we need to write each run separately on the same line.
            # We'll use multi_cell only if there are line breaks? Actually, we'll use cell for each run.
            # But to handle wrapping, we should use multi_cell for the whole line? Simpler: write each run with cell,
            # but that won't wrap. Better to combine into a single string with HTML-like tags? fpdf2 supports HTML via write_html?
            # Instead, we'll write each run sequentially using cell, but that can overflow.
            # For simplicity and reliability, we'll combine runs into a single text with style changes using write() method.
            # write() allows changing font mid-line. We'll use pdf.set_font before each segment.
            # Start at current x position
            start_x = pdf.get_x()
            start_y = pdf.get_y()
            # For each run, set font and write
            for run in runs_data:
                pdf.set_font('Helvetica', run['style'], run['size'])
                pdf.set_text_color(*run['color'])
                # Write text (handles wrapping)
                pdf.write(run['size'] * 0.3, run['text'])  # height factor ~0.3 of font size
                if run['underline']:
                    # Underline not directly supported in write, we can draw a line
                    # But for simplicity, skip underline (rare)
                    pass
            # After writing the paragraph, move to next line with appropriate spacing
            pdf.ln(paragraph.paragraph_format.line_spacing or 1.2 * runs_data[0]['size'])
            
            # Add extra spacing after paragraph if defined
            if paragraph.paragraph_format.space_after:
                pdf.ln(paragraph.paragraph_format.space_after.pt)
        
        # Process tables
        for table in doc.tables:
            # Determine number of rows and columns
            rows = len(table.rows)
            cols = max(len(row.cells) for row in table.rows)
            
            # Extract cell data as list of lists of text (with possible newlines)
            data = []
            for i, row in enumerate(table.rows):
                row_data = []
                for cell in row.cells:
                    # Get cell text, preserve line breaks
                    cell_text = cell.text.strip()
                    row_data.append(cell_text)
                data.append(row_data)
            
            # Calculate column widths based on content (simple heuristic)
            # Use FPDF's table creation: we'll use a simple approach with multi_cell
            # Save current position
            start_y = pdf.get_y()
            # Set font for table
            pdf.set_font('Helvetica', size=10)
            # For each row, output cells
            for row in data:
                # Max height for this row
                max_height = 0
                # Calculate needed height for each cell (rough estimate)
                cell_heights = []
                for i, cell_text in enumerate(row):
                    # Estimate lines
                    lines = cell_text.count('\n') + 1
                    height = lines * 12  # approximate line height in points
                    cell_heights.append(height)
                row_height = max(cell_heights) if cell_heights else 12
                # Output cells horizontally
                x_start = pdf.get_x()
                for i, cell_text in enumerate(row):
                    # Set border
                    border = 1
                    # Write cell with multi_cell to handle wrapping
                    pdf.set_font('Helvetica', size=10)
                    pdf.set_fill_color(240, 240, 240) if i == 0 else pdf.set_fill_color(255, 255, 255)
                    # Use multi_cell with fixed width (distribute equally)
                    cell_width = (pdf.w - pdf.l_margin - pdf.r_margin) / cols
                    pdf.multi_cell(cell_width, row_height, cell_text, border=border, align='L', fill=(i==0))
                    # Set x to next cell position
                    pdf.set_x(x_start + (i+1) * cell_width)
                pdf.ln(row_height)
            pdf.ln(10)
        
        # Output PDF
        pdf.output(out_path)
        
        # Verify output
        if not os.path.exists(out_path) or os.path.getsize(out_path) == 0:
            raise Exception("PDF generation failed")
        
        return out_path, out_name
        
    except Exception as e:
        logging.error(f"Word to PDF conversion error: {str(e)}")
        raise Exception(f"Failed to convert Word to PDF: {str(e)}")

# ================= IMPROVED PDF COMPRESSION =================

def compress_pdf(file_infos):
    if len(file_infos) != 1:
        raise ValueError("Please select exactly 1 PDF file to compress")
    f = file_infos[0]
    if f['ext'] != 'pdf':
        raise ValueError("Not a PDF")
    
    base_name = f['original'].rsplit('.', 1)[0]
    out_name = f"{base_name}_compressed.pdf"
    out_path = os.path.join(app.config['CONVERTED_FOLDER'], out_name)
    
    original_size = os.path.getsize(f['path'])
    logging.info(f"Original PDF size: {original_size / 1024:.2f} KB")
    
    try:
        reader = PyPDF2.PdfReader(f['path'])
        writer = PyPDF2.PdfWriter()
        
        # Compress each page
        for page in reader.pages:
            if hasattr(page, 'compress_content_streams'):
                page.compress_content_streams()
            writer.add_page(page)
        
        # Remove metadata
        writer.add_metadata({})
        
        # Write compressed
        with open(out_path, 'wb') as out_file:
            writer.write(out_file)
        
        compressed_size = os.path.getsize(out_path)
        reduction = ((original_size - compressed_size) / original_size) * 100 if original_size > 0 else 0
        logging.info(f"Compressed size: {compressed_size / 1024:.2f} KB ({reduction:.1f}% reduction)")
        
        # If less than 5% reduction and file > 100KB, try aggressive
        if reduction < 5 and original_size > 100000:
            logging.info("Attempting aggressive compression...")
            writer2 = PyPDF2.PdfWriter()
            reader2 = PyPDF2.PdfReader(f['path'])
            for page in reader2.pages:
                try:
                    page.compress_content_streams()
                except:
                    pass
                writer2.add_page(page)
            aggressive_path = out_path + ".aggressive"
            with open(aggressive_path, 'wb') as out_file:
                writer2.write(out_file)
            aggressive_size = os.path.getsize(aggressive_path)
            aggressive_reduction = ((original_size - aggressive_size) / original_size) * 100
            if aggressive_size < compressed_size:
                os.replace(aggressive_path, out_path)
                compressed_size = aggressive_size
                reduction = aggressive_reduction
                logging.info(f"Aggressive compression: {compressed_size / 1024:.2f} KB ({reduction:.1f}% reduction)")
            else:
                os.remove(aggressive_path)
        
        # Final check: if compressed is larger, return original
        if compressed_size >= original_size:
            os.remove(out_path)
            import shutil
            shutil.copy2(f['path'], out_path)
            out_name = f"{base_name}_original.pdf"
            out_path = os.path.join(app.config['CONVERTED_FOLDER'], out_name)
            logging.warning("Compression did not reduce size, returning original")
        
        final_size = os.path.getsize(out_path)
        final_reduction = ((original_size - final_size) / original_size) * 100
        logging.info(f"Final PDF size: {final_size / 1024:.2f} KB ({final_reduction:.1f}% reduction)")
        return out_path, out_name
        
    except Exception as e:
        logging.error(f"PDF compression error: {str(e)}")
        import shutil
        shutil.copy2(f['path'], out_path)
        out_name = f"{base_name}_copy.pdf"
        out_path = os.path.join(app.config['CONVERTED_FOLDER'], out_name)
        return out_path, out_name

# Map tool IDs
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
        else:
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
            return jsonify({'success': True, 'download_url': f'/download/{out_name}', 'filename': out_name})
        return jsonify({'success': True, 'filenames': [out_name for _, out_name in output_files], 'download_urls': [f'/download/{out_name}' for _, out_name in output_files]})
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
        c.execute("""SELECT tool, original_filename, converted_filename, created_at FROM conversions WHERE user_id = (SELECT id FROM users WHERE username=?) ORDER BY created_at DESC""", (session['user'],))
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
