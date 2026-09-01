#!/usr/bin/env python3
"""
Background Removal Tool for Logo Design

This module provides a simple interface to remove backgrounds from images
using the gateway API. It returns transparent PNG images suitable for
logo design and brand asset creation.

Usage:
    import sys
    import os
    skill_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    script_dir = os.path.join(skill_dir, 'script')
    if script_dir not in sys.path:
        sys.path.insert(0, script_dir)
    from remove_background import remove_background

    # Remove background from multiple images (batch processing)
    image_url_list = ['https://example.com/logo1.png', 'https://example.com/logo2.png']
    results = remove_background(image_url_list)

    for i, result in enumerate(results):
        if result['success']:
            print(f"Logo {i+1}: ✅ {result['result']}")
        else:
            print(f"Logo {i+1}: ❌ {result['error']}")

Tool Description:
    Background Removal Tool: Removes the background from images to create
    transparent PNG files. Use this tool when you need to:
    - Create transparent logo files for flexible brand applications
    - Remove solid/patterned backgrounds from generated logos
    - Prepare logos for placement on various colored backgrounds
    - Export logo assets for web, print, or merchandise use
"""

import os
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any

# Add _lib to path to import gateway client
# Path: skills/image-prompt-guide/logo-design/script/remove_background.py
# Target: skills/_lib/gateway_client.py
_current_dir = os.path.dirname(os.path.abspath(__file__))  # .../logo-design/script
_logo_design_dir = os.path.dirname(_current_dir)  # .../logo-design
_image_prompt_guide_dir = os.path.dirname(_logo_design_dir)  # .../image-prompt-guide
_skills_dir = os.path.dirname(_image_prompt_guide_dir)  # .../skills
_lib_path = os.path.join(_skills_dir, '_lib')
if _lib_path not in sys.path:
    sys.path.insert(0, _lib_path)

from gateway_client import GatewayClient  # noqa: E402


class BackgroundRemover:
    """
    Background Remover Client for creating transparent PNG images.

    This class uses the gateway API to remove backgrounds from images,
    returning transparent PNG files ideal for logo design workflows.
    """

    # Gateway function name for background removal
    FUNCTION_REMOVE_BACKGROUND = 'image_remove_background'

    def __init__(self):
        """Initialize BackgroundRemover with gateway client"""
        self.gateway_client = GatewayClient()

    def remove_background(self, image_url: str) -> dict[str, Any]:
        """
        Remove background from a single image.

        Args:
            image_url: URL of the image to process
                Example: 'https://sc02.alicdn.com/kf/A3e61b5ce465f40e5b3ee0d3c7c924d83Z.png'

        Returns:
            Dict with keys:
                - success (bool): Whether the operation succeeded
                - result (str): URL of the transparent PNG image (if successful)
                - error (str): Error message (if failed)

        Example:
            >>> remover = BackgroundRemover()
            >>> result = remover.remove_background('https://example.com/logo.png')
            >>> if result['success']:
            ...     print(f"Transparent image: {result['result']}")
        """
        if not image_url or not isinstance(image_url, str):
            return {
                'success': False,
                'error': 'Invalid image_url: must be a non-empty string'
            }

        # Strip whitespace from URL
        image_url = image_url.strip()

        try:
            # Call gateway API
            response = self.gateway_client.call(
                function_name=self.FUNCTION_REMOVE_BACKGROUND,
                payload={'image_url': image_url},
                timeout=120  # Background removal may take longer
            )

            if not response.get('success'):
                return {
                    'success': False,
                    'error': response.get('error', 'Gateway API call failed')
                }

            # Extract result URL from response
            data = response.get('data', {})

            # Handle different response structures
            result_url = None
            if isinstance(data, dict):
                # Try common response paths
                result_url = (
                    data.get('result') or
                    data.get('url') or
                    data.get('image_url') or
                    data.get('resultUrl')
                )

                # Check nested structure: data.result.url
                if not result_url and 'result' in data:
                    nested_result = data['result']
                    if isinstance(nested_result, dict):
                        result_url = nested_result.get('url') or nested_result.get('image_url')
                    elif isinstance(nested_result, str):
                        result_url = nested_result
            elif isinstance(data, str):
                result_url = data

            if not result_url:
                return {
                    'success': False,
                    'error': 'No result URL in API response',
                    'raw_response': data
                }

            return {
                'success': True,
                'result': result_url,
                'original_url': image_url
            }

        except Exception as e:
            import traceback
            return {
                'success': False,
                'error': f'Background removal failed: {str(e)}',
                'traceback': traceback.format_exc()
            }

    def remove_backgrounds(
        self,
        image_urls: list[str],
        continue_on_error: bool = True,
        max_workers: int = 16
    ) -> list[dict[str, Any]]:
        """
        Remove backgrounds from multiple images concurrently.

        Args:
            image_urls: List of image URLs to process
            continue_on_error: If True, continue processing even if some fail
            max_workers: Maximum number of concurrent workers (default: 16)

        Returns:
            List of result dicts, one per input URL (same order as input)

        Example:
            >>> remover = BackgroundRemover()
            >>> urls = ['https://example.com/logo1.png', 'https://example.com/logo2.png']
            >>> results = remover.remove_backgrounds(urls)
            >>> for i, result in enumerate(results):
            ...     if result['success']:
            ...         print(f"Logo {i+1}: ✅ {result['result']}")
        """
        if not image_urls:
            return []

        # Use dictionary to maintain order of results
        results_dict = {}

        # Process images concurrently with ThreadPoolExecutor
        with ThreadPoolExecutor(max_workers=min(max_workers, len(image_urls))) as executor:
            # Submit all tasks and map them to their index
            future_to_index = {
                executor.submit(self.remove_background, url): idx
                for idx, url in enumerate(image_urls)
            }

            # Collect results as they complete
            for future in as_completed(future_to_index):
                idx = future_to_index[future]
                try:
                    result = future.result()
                    results_dict[idx] = result

                    # Print progress
                    if result['success']:
                        print(f"✅ Image {idx + 1}/{len(image_urls)}: Background removed successfully")
                    else:
                        print(f"❌ Image {idx + 1}/{len(image_urls)}: {result.get('error', 'Unknown error')}")

                except Exception as e:
                    results_dict[idx] = {
                        'success': False,
                        'error': f'Task execution failed: {str(e)}',
                        'original_url': image_urls[idx]
                    }
                    print(f"❌ Image {idx + 1}/{len(image_urls)}: Task execution failed")

        # Return results in original order
        return [results_dict[i] for i in range(len(image_urls))]


# Convenience function for direct usage
def remove_background(image_url_list: list[str]) -> list[dict[str, Any]]:
    """
    Convenience function to remove backgrounds from multiple images concurrently.

    Args:
        image_url_list: List of image URLs to process

    Returns:
        List of result dicts, one per input URL (same order as input)
        Each dict contains: 'success', 'result' (URL if successful), and 'error' (if failed)

    Example:
        >>> image_url_list = [
        ...     'https://example.com/logo1.png',
        ...     'https://example.com/logo2.png',
        ...     'https://example.com/logo3.png'
        ... ]
        >>> results = remove_background(image_url_list)
        >>> for i, result in enumerate(results):
        ...     if result['success']:
        ...         print(f"Logo {i+1}: ✅ {result['result']}")
        ...     else:
        ...         print(f"Logo {i+1}: ❌ {result['error']}")
    """
    remover = BackgroundRemover()
    return remover.remove_backgrounds(image_url_list)


# Example usage (for testing)
if __name__ == '__main__':

    # Test image URLs
    TEST_URLS = [
        'https://sc02.alicdn.com/kf/A3e61b5ce465f40e5b3ee0d3c7c924d83Z.png',
        'https://sc02.alicdn.com/kf/H89219d5fa6a44d33876046a1e0fb8e9er.png',
    ]

    print('=' * 60)
    print('Background Removal Tool Test')
    print('=' * 60)
    print(f'Test images: {len(TEST_URLS)}')
    print()

    # Test batch processing
    print('>>> Testing batch background removal (concurrent)...')
    print()
    results = remove_background(TEST_URLS)

    print()
    print('=' * 60)
    print('Results Summary:')
    print('=' * 60)
    for i, result in enumerate(results):
        if result['success']:
            print(f'\n✅ Logo {i+1}: Success!')
            print(f'   Transparent URL: {result["result"]}')
        else:
            print(f'\n❌ Logo {i+1}: Failed')
            print(f'   Error: {result.get("error")}')
