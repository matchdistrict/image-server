import io
import logging
from PIL import Image as PILImage, ImageOps

logger = logging.getLogger(__name__)

class ImageService:
    @staticmethod
    def generate_thumbnail(image_bytes: bytes, max_width: int = 300) -> bytes:
        try:
            img = PILImage.open(io.BytesIO(image_bytes))
            
            # Correct orientation from EXIF
            try:
                img = ImageOps.exif_transpose(img)
            except Exception:
                pass
                
            # Maintain aspect ratio
            aspect_ratio = img.height / img.width
            target_height = int(max_width * aspect_ratio)
            
            # Resize using Lanczos scaling
            img.thumbnail((max_width, target_height), PILImage.Resampling.LANCZOS)
            
            output = io.BytesIO()
            # Save using the same format as source, defaulting to WEBP
            orig_format = img.format if img.format else "JPEG"
            img.save(output, format=orig_format, quality=80)
            return output.getvalue()
        except Exception as e:
            logger.error(f"Failed to generate thumbnail: {e}")
            raise ValueError(f"Failed to process image: {e}")

    @staticmethod
    def validate_and_optimize(image_bytes: bytes) -> bytes:
        try:
            img = PILImage.open(io.BytesIO(image_bytes))
            img.verify()  # Verify it's a valid image structure
            
            # Re-open after verify() since verify() closes file pointers
            img = PILImage.open(io.BytesIO(image_bytes))
            
            output = io.BytesIO()
            orig_format = img.format if img.format else "JPEG"
            img.save(output, format=orig_format, quality=85)
            return output.getvalue()
        except Exception as e:
            logger.error(f"Failed to validate and optimize image: {e}")
            raise ValueError(f"Invalid image format: {e}")

image_service = ImageService()
