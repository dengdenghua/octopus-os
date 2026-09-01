"""
Seedance Video Generation Skill for Echo Agent
Uses Volcano Engine Seedance API to generate videos from text, images, or existing videos.
"""

import base64
import json
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import requests


@dataclass
class SeedanceConfig:
    """Configuration for Seedance API."""

    api_key: str
    base_url: str = "https://ark.cn-beijing.volces.com/api/v3"
    model: str = "seedance"
    timeout: int = 300  # Video generation takes longer


class SeedanceVideoGenerator:
    """Generator for Seedance videos."""

    # Supported resolutions
    RESOLUTIONS = {
        "480p": (854, 480),
        "720p": (1280, 720),
        "1080p": (1920, 1080),
    }

    # Supported styles
    STYLES = ["default", "realistic", "anime", "3d", "cinematic", "documentary"]

    def __init__(self, config: SeedanceConfig | None = None):
        if config is None:
            api_key = os.environ.get("ARK_API_KEY")
            if not api_key:
                raise ValueError("ARK_API_KEY not found. Please set it in environment or config.")
            config = SeedanceConfig(api_key=api_key)
        self.config = config

    def _get_headers(self) -> dict[str, str]:
        """Get request headers."""
        return {
            "Authorization": f"Bearer {self.config.api_key}",
            "Content-Type": "application/json",
        }

    def _resolve_resolution(self, resolution: str) -> tuple:
        """Resolve resolution string to dimensions."""
        if resolution in self.RESOLUTIONS:
            return self.RESOLUTIONS[resolution]
        return self.RESOLUTIONS["720p"]  # Default

    def _encode_image(self, image_path: str) -> str:
        """Encode image to base64."""
        with open(image_path, "rb") as f:
            return base64.b64encode(f.read()).decode("utf-8")

    def _poll_task(self, task_id: str) -> dict[str, Any]:
        """Poll task status until completion."""
        url = f"{self.config.base_url}/tasks/{task_id}"

        for _ in range(60):  # Max 5 minutes
            try:
                response = requests.get(url, headers=self._get_headers(), timeout=30)
                response.raise_for_status()
                result = response.json()

                status = result.get("status")
                if status == "completed":
                    return result
                if status == "failed":
                    return {
                        "success": False,
                        "error": result.get("error", "Task failed"),
                        "error_code": "GENERATION_FAILED",
                    }

                # Still processing, wait
                time.sleep(5)
            except Exception as e:
                return {
                    "success": False,
                    "error": f"Polling failed: {str(e)}",
                    "error_code": "POLLING_FAILED",
                }

        return {"success": False, "error": "Generation timeout", "error_code": "TIMEOUT"}

    def generate(
        self,
        prompt: str,
        image_path: str | None = None,
        duration: int = 5,
        resolution: str = "720p",
        style: str = "default",
        motion_strength: float = 0.5,
        seed: int | None = None,
        fps: int = 24,
        output_dir: str | None = None,
    ) -> dict[str, Any]:
        """
        Generate video from text prompt or image.

        Args:
            prompt: Text description of desired video
            image_path: Path to input image (for image-to-video)
            duration: Video duration in seconds (1-10)
            resolution: Output resolution
            style: Video style preset
            motion_strength: Motion intensity 0.0-1.0
            seed: Random seed
            fps: Frames per second
            output_dir: Directory to save video

        Returns:
            Dict with generation results
        """
        # Validate inputs
        if not prompt or len(prompt) > 500:
            raise ValueError("Prompt must be 1-500 characters")

        if style not in self.STYLES:
            raise ValueError(f"Style must be one of: {self.STYLES}")

        if not 1 <= duration <= 10:
            raise ValueError("Duration must be 1-10 seconds")

        if not 0.0 <= motion_strength <= 1.0:
            raise ValueError("Motion strength must be 0.0-1.0")

        # Resolve resolution
        width, height = self._resolve_resolution(resolution)

        # Prepare request payload
        payload = {
            "model": self.config.model,
            "prompt": prompt,
            "duration": duration,
            "width": width,
            "height": height,
            "style": style,
            "motion_strength": motion_strength,
            "fps": fps,
        }

        if seed is not None:
            payload["seed"] = seed

        # Add image if provided
        if image_path:
            if not os.path.exists(image_path):
                raise ValueError(f"Image not found: {image_path}")
            payload["image"] = self._encode_image(image_path)

        # Submit generation task
        url = f"{self.config.base_url}/videos/generations"

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

        # Poll for completion
        task_id = result.get("task_id")
        if not task_id:
            return {
                "success": False,
                "error": "No task ID returned",
                "error_code": "GENERATION_FAILED",
            }

        task_result = self._poll_task(task_id)

        if not task_result.get("success", True):
            return task_result

        # Download video
        video_url = task_result.get("video_url")
        if not video_url:
            return {
                "success": False,
                "error": "No video URL in result",
                "error_code": "GENERATION_FAILED",
            }

        # Save locally
        output_path = Path(output_dir) if output_dir else Path("data/artifacts/videos")
        output_path.mkdir(parents=True, exist_ok=True)

        timestamp = int(time.time())
        filename = f"seedance_{timestamp}.mp4"
        local_path = output_path / filename

        try:
            video_response = requests.get(video_url, timeout=120)
            video_response.raise_for_status()
            local_path.write_bytes(video_response.content)
        except Exception as e:
            return {
                "success": False,
                "error": f"Download failed: {str(e)}",
                "error_code": "DOWNLOAD_FAILED",
            }

        return {
            "success": True,
            "video": {
                "url": video_url,
                "local_path": str(local_path),
                "duration": duration,
                "resolution": resolution,
                "width": width,
                "height": height,
                "fps": fps,
                "seed": seed,
            },
            "prompt": prompt,
            "style": style,
        }

    def extend(
        self,
        video_path: str,
        prompt: str,
        extend_duration: int = 3,
        output_dir: str | None = None,
    ) -> dict[str, Any]:
        """
        Extend existing video.

        Args:
            video_path: Path to existing video
            prompt: Description of continuation
            extend_duration: Seconds to extend
            output_dir: Output directory

        Returns:
            Dict with extension results
        """
        if not os.path.exists(video_path):
            raise ValueError(f"Video not found: {video_path}")

        if not 1 <= extend_duration <= 5:
            raise ValueError("Extend duration must be 1-5 seconds")

        # Encode video
        with open(video_path, "rb") as f:
            video_base64 = base64.b64encode(f.read()).decode("utf-8")

        payload = {
            "model": self.config.model,
            "video": video_base64,
            "prompt": prompt,
            "extend_duration": extend_duration,
        }

        url = f"{self.config.base_url}/videos/extend"

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
                "error_code": "EXTENSION_FAILED",
            }

        # Poll and download (similar to generate)
        task_id = result.get("task_id")
        if task_id:
            return self._poll_task(task_id)
            # ... download logic similar to generate

        return result


# Convenience functions
def generate_video(
    prompt: str,
    image_path: str | None = None,
    duration: int = 5,
    resolution: str = "720p",
    style: str = "default",
    motion_strength: float = 0.5,
    seed: int | None = None,
    fps: int = 24,
    output_dir: str | None = None,
    api_key: str | None = None,
) -> dict[str, Any]:
    """Generate video using Seedance API."""
    config = SeedanceConfig(api_key=api_key) if api_key else None
    generator = SeedanceVideoGenerator(config)
    return generator.generate(
        prompt=prompt,
        image_path=image_path,
        duration=duration,
        resolution=resolution,
        style=style,
        motion_strength=motion_strength,
        seed=seed,
        fps=fps,
        output_dir=output_dir,
    )


def extend_video(
    video_path: str,
    prompt: str,
    extend_duration: int = 3,
    output_dir: str | None = None,
    api_key: str | None = None,
) -> dict[str, Any]:
    """Extend existing video using Seedance API."""
    config = SeedanceConfig(api_key=api_key) if api_key else None
    generator = SeedanceVideoGenerator(config)
    return generator.extend(
        video_path=video_path,
        prompt=prompt,
        extend_duration=extend_duration,
        output_dir=output_dir,
    )


if __name__ == "__main__":
    # Test usage
    result = generate_video(
        prompt="一只金毛犬在草地上奔跑，阳光明媚",
        duration=5,
        resolution="720p",
        style="realistic",
    )
    print(json.dumps(result, indent=2, ensure_ascii=False))
