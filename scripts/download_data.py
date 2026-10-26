"""Download the third-party datasets used by the project into data/.

The files come from the IBM "RAG and Agentic AI" course and are not redistributed in this repository.
Usage: python scripts/download_data.py [--with-images]
"""
import argparse
import time
import urllib.request
import zipfile
from pathlib import Path

BASE = "https://cf-courses-data.s3.us.cloud-object-storage.appdomain.cloud"
FILES = {
    "California-Culinary-Map.txt": f"{BASE}/1r_mM6ZPYNxcFv65QkzubA/California-Culinary-Map.txt",
    "Recipes.json": f"{BASE}/hpTjb6liKBLVHQK0UgMi5A/Recipes.json",
    "Synthetic-User-Reviews.json": f"{BASE}/fQUs9wQ6aB6ts6fmkD2V2w/Synthetic-User-Reviews.json",
}
IMAGES_ZIP = ("synthetic-recipe-images.zip", f"{BASE}/5_Rr6ohviItzucyWk6nkrw/synthetic-recipe-images.zip")
DATA_DIR = Path(__file__).resolve().parents[1] / "data"
ATTEMPTS = 5


def fetch(name: str, url: str) -> Path:
    target = DATA_DIR / name
    if target.exists():
        print(f"skip   {name} (already present)")
        return target
    print(f"fetch  {name}")
    # Docker Desktop's DNS sometimes fails for a few seconds; retry with backoff instead of failing the build.
    # Download to a temporary name so an interrupted transfer is never mistaken for a complete file.
    partial = target.with_name(target.name + ".part")
    for attempt in range(1, ATTEMPTS + 1):
        try:
            urllib.request.urlretrieve(url, partial)
            break
        except OSError as exc:
            if attempt == ATTEMPTS:
                raise
            delay = 2**attempt
            print(f"retry  {name} in {delay}s ({exc})")
            time.sleep(delay)
    partial.replace(target)
    return target


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--with-images", action="store_true", help="also download the ~210 MB recipe image set")
    args = parser.parse_args()

    DATA_DIR.mkdir(exist_ok=True)
    for name, url in FILES.items():
        fetch(name, url)

    if args.with_images:
        archive = fetch(*IMAGES_ZIP)
        print("unzip  synthetic_recipe_images/")
        with zipfile.ZipFile(archive) as zf:
            zf.extractall(DATA_DIR)

    print(f"Done. Files are in {DATA_DIR}")


if __name__ == "__main__":
    main()
