from __future__ import annotations

import os
import shutil
import tempfile
import time
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlparse

from selenium import webdriver
from selenium.common.exceptions import NoSuchElementException, TimeoutException, WebDriverException
from selenium.webdriver.common.selenium_manager import SeleniumManager
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.edge.service import Service as EdgeService
from selenium.webdriver.remote.webelement import WebElement
from selenium.webdriver.support.ui import WebDriverWait

from .capture import sanitize_filename
from .models import CaptureArea, ElementDescriptor, ElementLocator


ELIBRO_PAGE_FIELD_SELECTOR = ".page-number"
ELIBRO_TITLE_SELECTOR = "div.sidebar-header div.brand div.title"
BROWSER_EDGE = "edge"
BROWSER_CHROME = "chrome"
SUPPORTED_BROWSERS = (BROWSER_EDGE, BROWSER_CHROME)


def _css_escape(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"')


def _safe_css_identifier(value: str) -> str | None:
    if not value:
        return None
    if any(character.isspace() for character in value):
        return None
    return value.replace("\\", "\\\\").replace(".", "\\.")


def default_user_data_dir(browser_name: str) -> Path | None:
    local_app_data = os.environ.get("LOCALAPPDATA", "")
    if not local_app_data:
        return None

    base_dir = Path(local_app_data)
    mapping = {
        BROWSER_EDGE: base_dir / "Microsoft" / "Edge" / "User Data",
        BROWSER_CHROME: base_dir / "Google" / "Chrome" / "User Data",
    }
    return mapping.get(browser_name)


class _FirstTagParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.descriptor: ElementDescriptor | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if self.descriptor is None:
            self.descriptor = ElementDescriptor(
                tag_name=tag,
                attributes={key: value or "" for key, value in attrs},
            )


def parse_html_snippet(snippet: str) -> ElementDescriptor | None:
    parser = _FirstTagParser()
    parser.feed(snippet.strip())
    descriptor = parser.descriptor
    if descriptor:
        descriptor.outer_html = snippet.strip()
        descriptor.description = descriptor.attributes.get("aria-label") or descriptor.tag_name
    return descriptor


def build_locator_from_descriptor(descriptor: ElementDescriptor) -> ElementLocator | None:
    tag_name = descriptor.tag_name or "input"
    attributes = descriptor.attributes

    id_value = attributes.get("id", "").strip()
    if id_value:
        return ElementLocator("css", f'[id="{_css_escape(id_value)}"]', id_value, "html")

    stable_attributes = []
    for attribute_name in ("data-e2e", "name", "aria-label", "placeholder", "type", "inputmode"):
        attribute_value = attributes.get(attribute_name, "").strip()
        if attribute_value:
            stable_attributes.append(f'[{attribute_name}="{_css_escape(attribute_value)}"]')

    classes = [token for token in attributes.get("class", "").split() if token]
    class_selector = "".join(
        f".{safe}"
        for token in classes[:3]
        if (safe := _safe_css_identifier(token)) is not None
    )

    selector = f"{tag_name}{class_selector}{''.join(stable_attributes)}"
    if selector != tag_name:
        description = attributes.get("aria-label") or attributes.get("name") or descriptor.tag_name
        return ElementLocator("css", selector, description, "html")

    return None


CSS_PATH_HELPER = """
function __stBuildCssPath(element) {
    if (!(element instanceof Element)) {
        return "";
    }
    const parts = [];
    while (element && element.nodeType === Node.ELEMENT_NODE) {
        let selector = element.nodeName.toLowerCase();
        if (element.id) {
            selector += "#" + CSS.escape(element.id);
            parts.unshift(selector);
            break;
        }
        let sibling = element;
        let nth = 1;
        while ((sibling = sibling.previousElementSibling)) {
            if (sibling.nodeName.toLowerCase() === element.nodeName.toLowerCase()) {
                nth += 1;
            }
        }
        selector += `:nth-of-type(${nth})`;
        parts.unshift(selector);
        element = element.parentElement;
    }
    return parts.join(" > ");
}
"""


PICKER_SCRIPT = f"""
(() => {{
    {CSS_PATH_HELPER}
    const previousState = window.__stPickerState || {{}};
    if (typeof previousState.cleanup === "function") {{
        previousState.cleanup();
    }}

    const box = document.createElement("div");
    box.style.position = "fixed";
    box.style.pointerEvents = "none";
    box.style.zIndex = "2147483647";
    box.style.border = "2px solid #1a73e8";
    box.style.background = "rgba(26,115,232,0.15)";
    box.style.display = "none";
    document.documentElement.appendChild(box);

    const state = {{
        picked: null,
        cleanup() {{
            document.removeEventListener("mousemove", moveHandler, true);
            document.removeEventListener("click", clickHandler, true);
            if (box.isConnected) {{
                box.remove();
            }}
            if (window.__stPickerState === state) {{
                window.__stPickerState = null;
            }}
        }}
    }};

    function draw(target) {{
        if (!(target instanceof Element)) {{
            box.style.display = "none";
            return;
        }}
        const rect = target.getBoundingClientRect();
        box.style.display = "block";
        box.style.left = `${{rect.left}}px`;
        box.style.top = `${{rect.top}}px`;
        box.style.width = `${{rect.width}}px`;
        box.style.height = `${{rect.height}}px`;
    }}

    function moveHandler(event) {{
        draw(event.target);
    }}

    function clickHandler(event) {{
        event.preventDefault();
        event.stopPropagation();
        draw(event.target);
        const rect = event.target.getBoundingClientRect();
        state.picked = {{
            cssPath: __stBuildCssPath(event.target),
            tagName: event.target.tagName.toLowerCase(),
            outerHTML: event.target.outerHTML ? event.target.outerHTML.slice(0, 1200) : "",
            ariaLabel: event.target.getAttribute("aria-label") || "",
            name: event.target.getAttribute("name") || "",
            id: event.target.id || "",
            rectLeft: rect.left,
            rectTop: rect.top,
            rectWidth: rect.width,
            rectHeight: rect.height
        }};
    }}

    document.addEventListener("mousemove", moveHandler, true);
    document.addEventListener("click", clickHandler, true);
    window.__stPickerState = state;
    return true;
}})();
"""


PICKER_CLEANUP_SCRIPT = """
if (window.__stPickerState && typeof window.__stPickerState.cleanup === 'function') {
    window.__stPickerState.cleanup();
} else {
    window.__stPickerState = null;
}
"""


PAGE_FIELD_SELECT_SCRIPT = """
const element = arguments[0];
if (!(element instanceof Element)) {
    return false;
}
element.scrollIntoView({block: 'center'});
if (typeof element.focus === 'function') {
    element.focus({preventScroll: true});
}
if (typeof element.select === 'function') {
    element.select();
    return true;
}
if (typeof element.setSelectionRange === 'function' && typeof element.value === 'string') {
    element.setSelectionRange(0, element.value.length);
    return true;
}
const selection = window.getSelection ? window.getSelection() : null;
if (selection) {
    selection.removeAllRanges();
    const range = document.createRange();
    range.selectNodeContents(element);
    selection.addRange(range);
    return true;
}
return false;
"""


PAGE_FIELD_CLEAR_SCRIPT = """
const element = arguments[0];
if (!(element instanceof Element)) {
    return '';
}
const fireInputEvents = (target) => {
    target.dispatchEvent(new Event('input', {bubbles: true}));
    target.dispatchEvent(new Event('change', {bubbles: true}));
};
if ('value' in element) {
    const prototype = Object.getPrototypeOf(element);
    const descriptor = prototype ? Object.getOwnPropertyDescriptor(prototype, 'value') : null;
    if (descriptor && typeof descriptor.set === 'function') {
        descriptor.set.call(element, '');
    } else {
        element.value = '';
    }
    fireInputEvents(element);
    return element.value || '';
}
if (element.isContentEditable) {
    element.textContent = '';
    fireInputEvents(element);
    return element.textContent || '';
}
return (element.textContent || '').trim();
"""


PAGE_FIELD_VALUE_SCRIPT = """
const element = arguments[0];
if (!(element instanceof Element)) {
    return '';
}
if ('value' in element && typeof element.value === 'string') {
    return element.value;
}
return (element.textContent || '').trim();
"""


ACTIVE_ELEMENT_SCRIPT = f"""
(() => {{
    {CSS_PATH_HELPER}
    const active = document.activeElement;
    if (!(active instanceof Element) || active === document.body) {{
        return null;
    }}
    return {{
        cssPath: __stBuildCssPath(active),
        tagName: active.tagName.toLowerCase(),
        outerHTML: active.outerHTML ? active.outerHTML.slice(0, 1200) : "",
        ariaLabel: active.getAttribute("aria-label") || "",
        name: active.getAttribute("name") || "",
        id: active.id || ""
    }};
}})();
"""


class BrowserSession:
    def __init__(self, driver_path: Path | None = None) -> None:
        self.driver_path = driver_path
        self.driver: Any | None = None
        self.browser_name = BROWSER_EDGE
        self.temp_profile_root: Path | None = None

    @property
    def is_open(self) -> bool:
        if self.driver is None:
            return False
        try:
            _ = self.driver.current_url
            return True
        except WebDriverException:
            return False

    def _cleanup_temp_profile(self) -> None:
        if self.temp_profile_root and self.temp_profile_root.exists():
            shutil.rmtree(self.temp_profile_root, ignore_errors=True)
        self.temp_profile_root = None

    def _copy_profile_tree(self, source: Path, destination: Path) -> None:
        ignored_directories = {
            "Cache",
            "Code Cache",
            "Crashpad",
            "DawnCache",
            "GPUCache",
            "GrShaderCache",
            "Media Cache",
            "OptimizationHints",
            "ShaderCache",
        }

        for root, directories, files in os.walk(source):
            root_path = Path(root)
            relative_root = root_path.relative_to(source)
            directories[:] = [name for name in directories if name not in ignored_directories]
            target_root = destination / relative_root
            target_root.mkdir(parents=True, exist_ok=True)

            for file_name in files:
                source_file = root_path / file_name
                target_file = target_root / file_name
                try:
                    shutil.copy2(source_file, target_file)
                except OSError:
                    continue

    def _prepare_profile_copy(self, user_data_dir: Path, profile_directory: str) -> tuple[Path, str]:
        profile_source = user_data_dir / profile_directory
        if not profile_source.exists():
            raise RuntimeError(f"The selected profile directory does not exist: {profile_source}")

        self._cleanup_temp_profile()
        temp_root = Path(tempfile.mkdtemp(prefix="screenshot_taker_profile_"))

        local_state_file = user_data_dir / "Local State"
        if local_state_file.exists():
            try:
                shutil.copy2(local_state_file, temp_root / "Local State")
            except OSError:
                pass

        for optional_name in ("First Run", "Last Version"):
            optional_file = user_data_dir / optional_name
            if optional_file.exists() and optional_file.is_file():
                try:
                    shutil.copy2(optional_file, temp_root / optional_name)
                except OSError:
                    pass

        self._copy_profile_tree(profile_source, temp_root / profile_directory)
        self.temp_profile_root = temp_root
        return temp_root, profile_directory

    def _create_edge_driver(self, options: webdriver.EdgeOptions) -> Any:
        manager_error: Exception | None = None
        try:
            resolved_binaries = SeleniumManager().binary_paths(["--browser", "edge", "--skip-driver-in-path"])
            resolved_path = resolved_binaries.get("driver_path", "")
            if resolved_path:
                service = EdgeService(executable_path=resolved_path)
                return webdriver.Edge(service=service, options=options)
        except Exception as exc:  # noqa: BLE001
            manager_error = exc

        if self.driver_path and self.driver_path.exists():
            try:
                service = EdgeService(executable_path=str(self.driver_path))
                return webdriver.Edge(service=service, options=options)
            except WebDriverException as exc:
                fallback_message = str(exc)
                if "only supports microsoft edge version" in fallback_message.lower():
                    raise RuntimeError(
                        "The local msedgedriver.exe is outdated for the installed Edge version. "
                        "Delete or replace that driver, or keep using the automatic driver resolution."
                    ) from exc
                raise RuntimeError(fallback_message) from exc

        if manager_error is not None:
            raise RuntimeError(str(manager_error)) from manager_error

        raise RuntimeError("Unable to start Microsoft Edge.")

    def launch(
        self,
        url: str,
        browser_name: str = BROWSER_EDGE,
        use_profile: bool = False,
        user_data_dir: str | Path | None = None,
        profile_directory: str = "Default",
    ) -> None:
        if self.is_open:
            if url:
                self.driver.get(url)
            return

        self._cleanup_temp_profile()
        self.browser_name = browser_name if browser_name in SUPPORTED_BROWSERS else BROWSER_EDGE
        if self.browser_name == BROWSER_EDGE:
            options = webdriver.EdgeOptions()
            options.use_chromium = True
        else:
            options = webdriver.ChromeOptions()

        options.add_argument("--start-maximized")
        options.add_argument("--disable-blink-features=AutomationControlled")
        options.add_argument("--remote-debugging-port=0")
        options.add_argument("--no-first-run")
        options.add_argument("--no-default-browser-check")
        options.add_experimental_option("excludeSwitches", ["enable-logging"])
        if use_profile:
            if not user_data_dir:
                raise RuntimeError("Select a browser data directory first.")
            copied_user_data_dir, normalized_profile_directory = self._prepare_profile_copy(
                Path(user_data_dir),
                profile_directory.strip() or "Default",
            )
            options.add_argument(f"--user-data-dir={copied_user_data_dir}")
            options.add_argument(f"--profile-directory={normalized_profile_directory}")

        try:
            if self.browser_name == BROWSER_EDGE:
                self.driver = self._create_edge_driver(options)
            else:
                self.driver = webdriver.Chrome(options=options)
        except WebDriverException as exc:
            message = str(exc)
            if "user data directory is already in use" in message.lower():
                raise RuntimeError(
                    "That browser profile is already in use. Close the browser using it or choose another profile."
                ) from exc
            raise RuntimeError(message) from exc
        except RuntimeError:
            self._cleanup_temp_profile()
            raise

        if url:
            self.driver.get(url)

    def close(self) -> None:
        if self.driver is None:
            self._cleanup_temp_profile()
            return
        try:
            self.driver.quit()
        finally:
            self.driver = None
            self._cleanup_temp_profile()

    def _require_driver(self) -> webdriver.Edge:
        if not self.is_open or self.driver is None:
            raise RuntimeError("Browser is not open.")
        return self.driver

    def _find(self, locator: ElementLocator) -> WebElement:
        driver = self._require_driver()
        by = By.CSS_SELECTOR if locator.strategy == "css" else By.XPATH
        return driver.find_element(by, locator.value)

    def validate_locator(self, locator: ElementLocator) -> bool:
        try:
            self._find(locator)
            return True
        except NoSuchElementException:
            return False

    def begin_picker(self) -> None:
        driver = self._require_driver()
        driver.execute_script(PICKER_SCRIPT)

    def stop_picker(self) -> None:
        if not self.is_open or self.driver is None:
            return
        try:
            self.driver.execute_script(PICKER_CLEANUP_SCRIPT)
        except WebDriverException:
            pass

    def get_picked_locator(self) -> ElementLocator | None:
        driver = self._require_driver()
        payload = driver.execute_script("return window.__stPickerState ? window.__stPickerState.picked : null;")
        if not payload:
            return None

        locator = ElementLocator(
            strategy="css",
            value=payload.get("cssPath") or "",
            description=payload.get("ariaLabel") or payload.get("name") or payload.get("id") or payload.get("tagName") or "picked element",
            source="picked",
        )
        driver.execute_script(PICKER_CLEANUP_SCRIPT)
        return locator if locator.value else None

    def get_active_element_locator(self) -> ElementLocator | None:
        driver = self._require_driver()
        payload = driver.execute_script(ACTIVE_ELEMENT_SCRIPT)
        if not payload:
            return None
        locator = ElementLocator(
            strategy="css",
            value=payload.get("cssPath") or "",
            description=payload.get("ariaLabel") or payload.get("name") or payload.get("id") or payload.get("tagName") or "active element",
            source="active",
        )
        return locator if locator.value else None

    def get_picked_capture_area(self) -> CaptureArea | None:
        driver = self._require_driver()
        payload = driver.execute_script("return window.__stPickerState ? window.__stPickerState.picked : null;")
        if not payload:
            return None

        metrics = driver.execute_script(
            """
            return {
                screenX: window.screenX || window.screenLeft || 0,
                screenY: window.screenY || window.screenTop || 0
            };
            """
        )
        driver.execute_script(PICKER_CLEANUP_SCRIPT)

        try:
            left = int(round(float(metrics.get("screenX", 0)) + float(payload.get("rectLeft", 0))))
            top = int(round(float(metrics.get("screenY", 0)) + float(payload.get("rectTop", 0))))
            width = int(round(float(payload.get("rectWidth", 0))))
            height = int(round(float(payload.get("rectHeight", 0))))
        except (TypeError, ValueError):
            return None

        area = CaptureArea(left, top, width, height)
        return area if area.is_valid() else None

    def go_to_page(
        self,
        locator: ElementLocator,
        page_number: int,
        wait_seconds: float,
        wait_callback: Callable[[float], None] | None = None,
    ) -> None:
        element = self._find(locator)
        driver = self._require_driver()
        driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", element)
        element.click()
        driver.execute_script(PAGE_FIELD_SELECT_SCRIPT, element)
        element.send_keys(Keys.CONTROL, "a")
        element.send_keys(Keys.DELETE)
        element.send_keys(Keys.BACKSPACE)
        try:
            WebDriverWait(driver, 1.5).until(
                lambda web_driver: (web_driver.execute_script(PAGE_FIELD_VALUE_SCRIPT, self._find(locator)) or "").strip() == ""
            )
        except TimeoutException:
            driver.execute_script(PAGE_FIELD_CLEAR_SCRIPT, self._find(locator))
            driver.execute_script(PAGE_FIELD_SELECT_SCRIPT, self._find(locator))
        element.send_keys(str(page_number))
        element.send_keys(Keys.ENTER)

        expected = str(page_number)
        try:
            WebDriverWait(driver, max(5.0, wait_seconds + 4.0)).until(
                lambda web_driver: (web_driver.execute_script(PAGE_FIELD_VALUE_SCRIPT, self._find(locator)) or "").strip() == expected
            )
        except TimeoutException:
            pass
        if wait_seconds > 0:
            if wait_callback is not None:
                wait_callback(wait_seconds)
            else:
                time.sleep(wait_seconds)

    def read_page_value(self, locator: ElementLocator) -> str:
        element = self._find(locator)
        driver = self._require_driver()
        return str(driver.execute_script(PAGE_FIELD_VALUE_SCRIPT, element) or "").strip()

    def page_title(self) -> str:
        driver = self._require_driver()
        return driver.title or ""

    def suggest_base_name(self, url: str) -> str:
        if self.is_open and self.driver is not None:
            try:
                title_element = self.driver.find_element(By.CSS_SELECTOR, ELIBRO_TITLE_SELECTOR)
                title = title_element.get_attribute("title") or title_element.text.strip()
                if title:
                    return sanitize_filename(title)
            except NoSuchElementException:
                pass

            try:
                title = self.driver.title.strip()
                if title:
                    return sanitize_filename(title)
            except WebDriverException:
                pass

        parsed = urlparse(url)
        return sanitize_filename(parsed.netloc or "capture")


def locator_from_html_snippet(snippet: str) -> ElementLocator | None:
    descriptor = parse_html_snippet(snippet)
    if descriptor is None:
        return None
    return build_locator_from_descriptor(descriptor)
