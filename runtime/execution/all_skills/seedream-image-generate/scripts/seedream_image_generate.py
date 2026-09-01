"""
Seedream Image Generation Skill for Echo Agent
Uses Volcano Engine Seedream API to generate images from text prompts.
"""

import json
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import requests


@dataclass
class SeedreamConfig:
    """Configuration for Seedream API."""

    api_key: str
    base_url: str = "https://ark.cn-beijing.volces.com/api/v3"
    model: str = "seedream"
    timeout: int = 120


class SeedreamImageGenerator:
    """Generator for Seedream images."""

    # Supported aspect ratios mapping to dimensions
    RATIO_MAP = {
        "1:1": (1024, 1024),
        "16:9": (1920, 1080),
        "9:16": (1080, 1920),
        "4:3": (1440, 1080),
        "3:4": (1080, 1440),
    }

    # Supported styles
    STYLES = [
        "default",
        "anime",
        "realistic",
        "oil_painting",
        "watercolor",
        "3d",
        "pixel_art",
        "chinese_ink",
        "ukiyo_e",
        "sketch",
        "cyberpunk",
    ]

    def __init__(self, config: SeedreamConfig | None = None):
        if config is None:
            api_key = os.environ.get("ARK_API_KEY")
            if not api_key:
                raise ValueError("ARK_API_KEY not found. Please set it in environment or config.")
            config = SeedreamConfig(api_key=api_key)
        self.config = config

    def _get_headers(self) -> dict[str, str]:
        """Get request headers."""
        return {
            "Authorization": f"Bearer {self.config.api_key}",
            "Content-Type": "application/json",
        }

    def _resolve_dimensions(
        self, width: int | None = None, height: int | None = None, ratio: str | None = None
    ) -> tuple:
        """Resolve final dimensions from width/height or ratio."""
        if ratio and ratio in self.RATIO_MAP:
            return self.RATIO_MAP[ratio]

        if width and height:
            return (width, height)

        # Default to 1:1
        return (1024, 1024)

    def generate(
        self,
        prompt: str,
        width: int | None = None,
        height: int | None = None,
        ratio: str | None = None,
        style: str = "default",
        seed: int | None = None,
        negative_prompt: str = "",
        num_images: int = 1,
        output_dir: str | None = None,
    ) -> dict[str, Any]:
        """
        Generate image(s) from text prompt.

        Args:
            prompt: Text description of desired image
            width: Image width in pixels
            height: Image height in pixels
            ratio: Shortcut aspect ratio (overrides width/height)
            style: Art style preset
            seed: Random seed for reproducibility
            negative_prompt: Elements to exclude
            num_images: Number of images to generate (1-4)
            output_dir: Directory to save images

        Returns:
            Dict with generation results
        """
        # Validate inputs
        if not prompt or len(prompt) > 1000:
            raise ValueError("Prompt must be 1-1000 characters")

        if style not in self.STYLES:
            raise ValueError(f"Style must be one of: {self.STYLES}")

        if not 1 <= num_images <= 4:
            raise ValueError("num_images must be 1-4")

        # Resolve dimensions
        final_width, final_height = self._resolve_dimensions(width, height, ratio)

        # Prepare request payload
        payload = {
            "model": self.config.model,
            "prompt": prompt,
            "width": final_width,
            "height": final_height,
            "style": style,
            "n": num_images,
        }

        if seed is not None:
            payload["seed"] = seed
        if negative_prompt:
            payload["negative_prompt"] = negative_prompt

        # Make API request
        url = f"{self.config.base_url}/images/generations"

        try:
            response = requests.post(
                url,
                headers=self._get_headers(),
                json=payload,
                timeout=self.config.timeout,
            )
            response.raise_for_status()
            result = response.json()

        except requests.exceptions.RequestException as e:
            return {
                "success": False,
                "error": f"API request failed: {str(e)}",
                "error_code": "GENERATION_FAILED",
            }

        # Process and save images
        images = []
        output_path = Path(output_dir) if output_dir else Path("data/artifacts/images")
        output_path.mkdir(parents=True, exist_ok=True)

        timestamp = int(time.time())

        for i, img_data in enumerate(result.get("data", [])):
            img_url = img_data.get("url")
            if not img_url:
                continue

            # Download image
            try:
                img_response = requests.get(img_url, timeout=60)
                img_response.raise_for_status()

                # Save locally
                filename = f"seedream_{timestamp}_{i:03d}.png"
                local_path = output_path / filename
                local_path.write_bytes(img_response.content)

                images.append(
                    {
                        "url": img_url,
                        "local_path": str(local_path),
                        "width": final_width,
                        "height": final_height,
                        "seed": seed,
                    }
                )
            except Exception as e:
                images.append(
                    {
                        "url": img_url,
                        "local_path": None,
                        "error": str(e),
                    }
                )

        return {
            "success": len(images) > 0,
            "images": images,
            "prompt": prompt,
            "style": style,
            "dimensions": {"width": final_width, "height": final_height},
        }


# Convenience function for direct usage
def generate_image(
    prompt: str,
    width: int | None = None,
    height: int | None = None,
    ratio: str | None = None,
    style: str = "default",
    seed: int | None = None,
    negative_prompt: str = "",
    num_images: int = 1,
    output_dir: str | None = None,
    api_key: str | None = None,
) -> dict[str, Any]:
    """
    Generate image using Seedream API.

    This is the main entry point used by Echo Agent skill system.
    """
    config = None
    if api_key:
        config = SeedreamConfig(api_key=api_key)

    generator = SeedreamImageGenerator(config)
    return generator.generate(
        prompt=prompt,
        width=width,
        height=height,
        ratio=ratio,
        style=style,
        seed=seed,
        negative_prompt=negative_prompt,
        num_images=num_images,
        output_dir=output_dir,
    )


if __name__ == "__main__":
    # Test usage
    result = generate_image(
        prompt="一只可爱的橘猫在樱花树下睡觉，日系动漫风格",
        ratio="1:1",
        style="anime",
        seed=42,
    )
    print(json.dumps(result, indent=2, ensure_ascii=False))
