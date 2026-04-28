# Universal Screenshot Taker

Universal Screenshot Taker is a Windows desktop application for capturing page screenshots from browser-based readers and turning them into ZIP, PDF, searchable PDF, or OCR-derived text formats.

It supports:

- automatic page capture from a configurable page-number field
- semi-automatic and manual capture workflows
- importing an existing PDF as page images and continuing from there
- OCR with Tesseract, Ollama, and Surya
- export to ZIP, PDF, searchable PDF, TXT, Markdown, HTML, EPUB, and DOCX
- English, Spanish, and Chinese UI

Repository:

- [https://github.com/neura-neura/universal-screenshot-taker](https://github.com/neura-neura/universal-screenshot-taker)

## Main Features

- Open Microsoft Edge or Chrome and capture screenshots from virtually any page.
- Reuse your browser profile safely by launching a copied profile with your existing session data.
- Configure the page field by preset, CSS selector, HTML snippet, active element, or picker.
- Select the capture area by drag, two corners, or directly from an element in the browser.
- Keep a transparent overlay visible while capturing.
- Reorder captured pages with drag and drop.
- Re-capture or delete pages from the gallery context menu.
- Import a PDF and load all its pages as images into the gallery.
- Export image pages to ZIP or PDF.
- Generate searchable PDFs with Tesseract.
- Generate OCR text exports with Ollama or Surya.
- Preserve page images in rich OCR exports when the AI indicates an image or diagram should be kept.

## System Requirements

- Windows 10 or Windows 11
- Python 3.12 for running from source
- Microsoft Edge or Google Chrome
- Tesseract OCR if you want searchable PDFs with the Traditional OCR method
- Ollama if you want local AI OCR with Ollama
- optional GPU for faster local AI OCR

## End-User Installation

### Option 1: Use the installer

After building the project, the installer will be created at:

- `installer-output\UniversalScreenshotTaker-Setup.exe`

Run the installer and follow the wizard. The installer:

- installs the app into your user profile
- adds a Start Menu shortcut
- can optionally create a Desktop shortcut
- registers an uninstaller in Windows Apps & Features

### Option 2: Run from source

1. Clone the repository.
2. Create and activate a virtual environment.
3. Install dependencies.
4. Run the application.

PowerShell:

```powershell
python -m venv venv
.\venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r requirements.txt
python .\script.py
```

## OCR Setup

### Traditional OCR with Tesseract

Traditional OCR is the mode that produces a searchable PDF with text you can select and search inside the exported PDF.

Install Tesseract on Windows:

```powershell
winget install -e --id UB-Mannheim.TesseractOCR
```

The app can also install missing Tesseract language files into its managed runtime folder when needed.

### AI OCR with Ollama

Install Ollama:

```powershell
winget install -e --id Ollama.Ollama
```

Start the Ollama server if it is not already running:

```powershell
ollama serve
```

Then open the app, choose `AI model (Ollama)`, select or type a model, and use `Pull model`.

Examples:

- `glm-ocr`
- `qwen3-vl:4b`
- `openbmb/minicpm-v4.5:8b`
- `richardyoung/olmocr2:7b-q8`

### Experimental OCR with Surya

The app includes an experimental Surya mode. Use the `Install Surya` button in the UI if you want to enable it in your environment.

## How to Use the App

### Start a browser session

1. Open the app.
2. Choose Edge or Chrome.
3. Enter the target URL if you want the app to navigate automatically.
4. If needed, enable `Use my browser data`.
5. Click `Open browser`.

### Configure the page field

Choose how the app should locate the page-number input:

- eLibro preset
- CSS selector
- HTML snippet
- selected in browser
- active element

Once configured, the app can navigate pages automatically or semi-automatically.

### Select the capture area

Choose one of these methods:

- Drag rectangle
- Two corners
- Select in browser

Then click `Start area selection`.

### Capture pages

Available modes:

- Automatic range
- Semi-automatic
- Manual

The gallery on the right shows captured pages in order. You can:

- drag pages to reorder them
- right-click a page to re-capture or delete it
- double-click a page to open it with the system image viewer

### Import a PDF

Use `Import PDF` to load an existing PDF into the gallery as page images.

When you import a PDF:

- its pages are rasterized into temporary images
- the export base name is set to the same name as the imported PDF
- you can still add normal browser captures afterward

## Export Options

### ZIP

Exports the current image pages into a ZIP archive.

### PDF

Exports the current image pages into a standard image-based PDF.

### PDF + OCR

Behavior depends on the OCR method:

- `Traditional (Tesseract)`: exports a searchable PDF with selectable text
- `AI model (Ollama)`: exports a new text-based PDF derived from the AI OCR output
- `Surya`: exports a text-derived PDF using Surya output

### AI OCR text export

When using Ollama or Surya, you can export OCR text to:

- `.txt`
- `.md`
- `.html`
- `.epub`
- `.docx`

The text export pipeline normalizes common OCR line-break and hyphenation issues.

## Build the EXE

The project includes a PyInstaller build script.

```powershell
.\build_exe.ps1
```

Output:

- `dist\UniversalScreenshotTaker.exe`

The build script also regenerates the app icon before building.

## Build the Installer

The project includes an Inno Setup script and a PowerShell wrapper that builds the EXE first and then compiles the installer.

```powershell
.\build_installer.ps1
```

Output:

- `installer-output\UniversalScreenshotTaker-Setup.exe`

If Inno Setup is not installed, the script tries to install it using `winget`.

## Project Structure

```text
assets/                         Icon assets
installer/                     Inno Setup installer script
screenshot_taker/              Main application package
tools/                         Helper scripts such as icon generation
build_exe.ps1                  Builds the EXE with PyInstaller
build_installer.ps1            Builds the EXE and the installer
script.py                      Application entry point
universal_screenshot_taker.spec PyInstaller spec file
```

## Privacy and Security

- The repository does not include API keys or private credentials.
- The app stores user settings locally with `QSettings`.
- Browser-profile mode uses a temporary copy of the selected profile instead of editing your live browser profile directly.
- Temporary screenshots are stored in a temp subfolder and cleaned up when the app closes or when you start a new session.

## Troubleshooting

### Tesseract is missing

Install it:

```powershell
winget install -e --id UB-Mannheim.TesseractOCR
```

### Ollama is installed but OCR does not connect

Check that the server is running:

```powershell
ollama serve
```

Then use the app's `Test Ollama` button.

### A selected Tesseract language fails

Use the app's `Install selected language(s)` button. The app can manage missing language data in its own runtime folder.

### Browser profile does not open correctly

Close the normal browser first, then try again. Some profile files may be locked if the regular browser is still using them.

### Searchable PDF is required

Use `Traditional (Tesseract)` and export `PDF + OCR`. That is the path intended for searchable, selectable PDF text over scanned pages.

## Development Notes

- The project currently targets Windows first.
- The installer is built with Inno Setup.
- The standalone executable is built with PyInstaller.
- The default browser automation path prefers Selenium Manager and falls back to a bundled `msedgedriver.exe` when available.

## License

This project is licensed under the MIT License. See [LICENSE](LICENSE).
