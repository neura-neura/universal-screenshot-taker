from __future__ import annotations

import os
import re
import time
from pathlib import Path

from PIL import Image
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.edge.service import Service as EdgeService
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait

from .runtime import bundled_resource_path


def run_legacy_cli() -> None:
    url = input("Enter the URL of the book page: ").strip()
    start_page = int(input("Enter the starting page number: "))
    end_page = int(input("Enter the ending page number: "))

    driver_path = bundled_resource_path("msedgedriver.exe")
    options = webdriver.EdgeOptions()
    options.use_chromium = True
    options.add_argument("--start-maximized")
    options.add_argument("--disable-blink-features=AutomationControlled")
    options.add_experimental_option("excludeSwitches", ["enable-logging"])

    if driver_path.exists():
        service = EdgeService(executable_path=str(driver_path))
        driver = webdriver.Edge(service=service, options=options)
    else:
        driver = webdriver.Edge(options=options)

    wait = WebDriverWait(driver, 30)
    image_paths: list[Path] = []

    try:
        driver.get(url)

        print("\n=== START SESSION ===")
        print("1. Sign in on the website if needed.")
        print("2. Navigate to the viewer page.")
        print("3. When the first page is visible, press ENTER here.")
        input("-> Press ENTER when ready <-")

        page_input = wait.until(EC.presence_of_element_located((By.CLASS_NAME, "page-number")))

        title_element = wait.until(
            EC.presence_of_element_located((By.CSS_SELECTOR, "div.sidebar-header div.brand div.title"))
        )
        book_title = title_element.get_attribute("title") or title_element.text.strip() or "capture"
        book_title_clean = re.sub(r'[\\/:*?"<>|]', "_", book_title)

        page_input.clear()
        page_input.send_keys(str(start_page))
        page_input.send_keys(Keys.ENTER)
        time.sleep(4)

        driver.execute_script(
            """
            window.clickCount = 0;
            window.clickPoints = [];
            document.addEventListener('click', function(event) {
                if (window.clickCount < 2) {
                    window.clickPoints.push({x: event.clientX, y: event.clientY});
                    window.clickCount++;
                }
            }, false);
            """
        )

        print("\nNow click two corners in the Edge window:")
        print("   First click  -> top-left corner")
        print("   Second click -> bottom-right corner")

        while True:
            time.sleep(0.5)
            if driver.execute_script("return window.clickCount;") >= 2:
                points = driver.execute_script("return window.clickPoints;")
                left, top = points[0]["x"], points[0]["y"]
                right, bottom = points[1]["x"], points[1]["y"]
                break

        print(f"Selected area: ({left}, {top}) -> ({right}, {bottom})")

        def capture_page(page_number: int) -> Path:
            temp_path = Path(f"temp_{page_number}.png")
            driver.save_screenshot(str(temp_path))
            image = Image.open(temp_path)
            try:
                cropped = image.crop((left, top, right, bottom))
                output_path = Path(f"page_{page_number:03d}.png")
                cropped.save(output_path)
                return output_path
            finally:
                image.close()
                if temp_path.exists():
                    os.remove(temp_path)

        current_page = start_page
        while current_page <= end_page:
            print(f"Capturing page {current_page}...")
            image_paths.append(capture_page(current_page))

            if current_page < end_page:
                next_page = current_page + 1
                page_input = wait.until(EC.presence_of_element_located((By.CLASS_NAME, "page-number")))
                page_input.clear()
                page_input.send_keys(str(next_page))
                page_input.send_keys(Keys.ENTER)
                wait.until(
                    lambda web_driver: web_driver.find_element(By.CLASS_NAME, "page-number").get_attribute("value")
                    == str(next_page)
                )
                time.sleep(5)

            current_page += 1

        print("\nCreating PDF...")
        images = [Image.open(image_path).convert("RGB") for image_path in image_paths]
        try:
            pdf_path = Path(f"{book_title_clean}_{start_page}-{end_page}.pdf")
            images[0].save(
                pdf_path,
                "PDF",
                resolution=100.0,
                save_all=True,
                append_images=images[1:],
            )
            print(f"\nDone. PDF saved as: {pdf_path}")
        finally:
            for image in images:
                image.close()

        for image_path in image_paths:
            if image_path.exists():
                os.remove(image_path)
    finally:
        driver.quit()
