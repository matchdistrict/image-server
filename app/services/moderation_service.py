import io
import logging
from PIL import Image as PILImage

logger = logging.getLogger(__name__)

class ModerationService:
    @staticmethod
    def check_nsfw(image_bytes: bytes) -> bool:
        """
        Checks the image for potential NSFW content using a fast, standard skin-tone heuristic.
        Converts the image to a low-res map (100x100) and counts pixels matching common human skin RGB ranges:
        - R > 95, G > 40, B > 20
        - max(R,G,B) - min(R,G,B) > 15
        - |R - G| > 15, R > G, R > B
        If > 40% of the pixels match, the image is flagged as potential NSFW.
        """
        try:
            img = PILImage.open(io.BytesIO(image_bytes))
            # Downscale for performance
            img = img.resize((100, 100))
            rgb_img = img.convert("RGB")
            
            skin_pixels = 0
            total_pixels = 10000
            
            for y in range(100):
                for x in range(100):
                    r, g, b = rgb_img.getpixel((x, y))
                    if r > 95 and g > 40 and b > 20:
                        max_val = max(r, g, b)
                        min_val = min(r, g, b)
                        if (max_val - min_val > 15) and abs(r - g) > 15 and r > g and r > b:
                            skin_pixels += 1
                            
            skin_ratio = skin_pixels / total_pixels
            logger.info(f"NSFW evaluation completed. Skin pixel ratio: {skin_ratio:.2%}")
            return skin_ratio > 0.40
        except Exception as e:
            logger.error(f"Failed to analyze image for NSFW content: {e}")
            return False

moderation_service = ModerationService()
