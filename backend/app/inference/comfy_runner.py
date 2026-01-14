import io
import json
import time
import uuid
import base64
import requests
import random
from PIL import Image
from typing import Any, Dict, Optional
import os
from ..config import settings
from ..logger import logger

def pil_to_base64(img: Image.Image) -> str:
    """Convert PIL Image to base64 string"""
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    buf.seek(0)
    return base64.b64encode(buf.getvalue()).decode("utf-8")

def base64_to_pil(b64_str: str) -> Image.Image:
    """Convert base64 string to PIL Image"""
    img_bytes = base64.b64decode(b64_str)
    return Image.open(io.BytesIO(img_bytes))

def build_comfy_workflow(
    child_photo_filename: str,
    illustration_filename: str,
    prompt: str,
    negative_prompt: str = "",
    mask_filename: Optional[str] = None,
    use_alpha_for_mask: bool = False,
    seed: Optional[int] = None,
) -> dict:
    if not negative_prompt:
        negative_prompt = "plastic skin, deformed, cross-eyed, mismatched pupils, crooked teeth, bruises under the eyes, red nose, pink nose, extra teeth, oversized eyes, long neck, strabismus, big teeth, makeup, different color eyes, heterochromia, mismatched eyes, squint, misaligned eyes, diverse eyes"
    
    base_dir = os.path.dirname(__file__)
    workflow_api_path = os.path.join(base_dir, "workflow_api.json")
    if os.path.exists(workflow_api_path):
        try:
            with open(workflow_api_path, "r", encoding="utf-8") as f:
                prompt_dict = json.load(f)
            for node_id, node in prompt_dict.items():
                if not isinstance(node, dict):
                    continue
                if node.get("class_type") == "LoadImage":
                    current = node.get("inputs", {}).get("image")
                    if isinstance(current, str):
                        low = current.lower()
                        if "photo" in low:
                            node.setdefault("inputs", {})["image"] = child_photo_filename
                        elif "illustr" in low or "mask" in low:
                            node.setdefault("inputs", {})["image"] = illustration_filename
            for node in prompt_dict.values():
                if isinstance(node, dict) and node.get("class_type") == "CLIPTextEncode":
                    inputs = node.setdefault("inputs", {})
                    if "text" in inputs and isinstance(inputs.get("text"), str):
                        if not inputs.get("text") or "girl" in inputs.get("text", "") or "boy" in inputs.get("text", ""):
                            inputs["text"] = prompt
                        else:
                            if not inputs.get("text"):
                                inputs["text"] = negative_prompt
            if seed is not None:
                for node in prompt_dict.values():
                    if isinstance(node, dict) and node.get("class_type") == "KSampler":
                        node.setdefault("inputs", {})["seed"] = seed
            return prompt_dict
        except Exception as e:
            logger.warning(f"Failed to load API workflow, falling back to UI workflow: {e}")

    workflow_path = os.path.join(base_dir, "workflow.json")
    try:
        with open(workflow_path, "r", encoding="utf-8") as f:
            workflow_data = json.load(f)
    except FileNotFoundError:
        logger.error(f"Workflow file not found: {workflow_path}")
        raise RuntimeError(f"Workflow file not found: {workflow_path}")
    except json.JSONDecodeError as e:
        logger.error(f"Invalid JSON in workflow file: {e}")
        raise RuntimeError(f"Invalid JSON in workflow file: {e}")
    
    if isinstance(workflow_data, dict) and "nodes" not in workflow_data and all(
        isinstance(v, dict) and "class_type" in v and "inputs" in v for v in workflow_data.values()
    ):
        prompt_dict = workflow_data
        try:
            if "64" in prompt_dict and prompt_dict["64"].get("class_type") == "LoadImage":
                prompt_dict["64"].setdefault("inputs", {})["image"] = child_photo_filename
            if "10" in prompt_dict and prompt_dict["10"].get("class_type") == "LoadImage":
                prompt_dict["10"].setdefault("inputs", {})["image"] = illustration_filename
            if "150" in prompt_dict and prompt_dict["150"].get("class_type") == "LoadImage":
                prompt_dict["150"].setdefault("inputs", {})["image"] = mask_filename or illustration_filename
        except Exception:
            pass

        for node in prompt_dict.values():
            if isinstance(node, dict) and node.get("class_type") == "CLIPTextEncode":
                inputs = node.setdefault("inputs", {})
                if "text" in inputs and (inputs.get("text") is None or inputs.get("text") == ""):
                    inputs["text"] = prompt

        for node in prompt_dict.values():
            if node.get("class_type") == "ACN_ControlNetLoaderAdvanced":
                inputs = node.setdefault("inputs", {})
                cnet = inputs.get("cnet")
                if isinstance(cnet, str) and cnet:
                    try:
                        inputs["cnet"] = os.path.basename(cnet) or "control_v11p_sd15_lineart.pth"
                    except Exception:
                        inputs["cnet"] = "control_v11p_sd15_lineart.pth"
                if not inputs.get("cnet"):
                    inputs["cnet"] = "control_v11p_sd15_lineart.pth"

        for node in prompt_dict.values():
            if node.get("class_type") == "ImageToMask":
                if use_alpha_for_mask:
                    node.setdefault("inputs", {})["channel"] = "alpha"

        return prompt_dict
    
    nodes_by_id = {str(n["id"]): n for n in workflow_data["nodes"]}
    prompt_dict: Dict[str, Dict[str, Any]] = {}
    input_name_by_index: Dict[str, Dict[int, str]] = {}
    for node_id, node in nodes_by_id.items():
        prompt_dict[node_id] = {"class_type": node["type"], "inputs": {}}
        slot_map: Dict[int, str] = {}
        for idx, inp in enumerate(node.get("inputs", [])):
            if isinstance(inp, dict) and "name" in inp:
                slot_map[idx] = inp["name"]
        input_name_by_index[node_id] = slot_map

    for link in workflow_data.get("links", []):
        try:
            _, src_node, src_slot, dst_node, dst_slot, _ = link
            dst_node_id = str(dst_node)
            src_node_id = str(src_node)
            input_name = input_name_by_index.get(dst_node_id, {}).get(dst_slot)
            if input_name:
                prompt_dict[dst_node_id]["inputs"][input_name] = [src_node_id, src_slot]
        except Exception:
            continue

    for node_id, node in nodes_by_id.items():
        if node.get("type") == "LoadImage":
            widgets = node.get("widgets_values", [])
            filename_hint = (widgets[0] if widgets else "").lower()
            if "photo" in filename_hint:
                prompt_dict[node_id]["inputs"]["image"] = child_photo_filename
            elif "illustr" in filename_hint:
                prompt_dict[node_id]["inputs"]["image"] = illustration_filename
            elif "mask" in filename_hint:
                prompt_dict[node_id]["inputs"]["image"] = mask_filename or illustration_filename
    for node_id, node in nodes_by_id.items():
        if node.get("type") == "CLIPTextEncode":
            if node_id in ("6",):
                prompt_dict[node_id]["inputs"]["text"] = prompt
            elif node_id in ("19",):
                prompt_dict[node_id]["inputs"]["text"] = negative_prompt
    for node_id, node in nodes_by_id.items():
        if node.get("type") == "CheckpointLoaderSimple":
            ckpt = None
            if isinstance(node.get("widgets_values"), list) and node["widgets_values"]:
                ckpt = node["widgets_values"][0]
            prompt_dict[node_id]["inputs"]["ckpt_name"] = ckpt or "dreamshaper_8.safetensors"

    if "64" in prompt_dict:
        prompt_dict["64"]["inputs"]["image"] = child_photo_filename
    if "10" in prompt_dict:
        prompt_dict["10"]["inputs"]["image"] = illustration_filename
    if "150" in prompt_dict:
        prompt_dict["150"]["inputs"]["image"] = illustration_filename
    if "3" in prompt_dict:
        apply_node_id = None
        for nid, n in nodes_by_id.items():
            if n.get("type") in ("IPAdapterFaceID", "IPAdapterAdvanced", "IPAdapterSimple"):
                apply_node_id = nid
                break
        if apply_node_id is not None:
            prompt_dict["3"]["inputs"]["model"] = [apply_node_id, 0]
        else:
            prompt_dict["3"]["inputs"]["model"] = ["4", 0]
    if "140" in prompt_dict:
        prompt_dict["140"]["inputs"]["images"] = ["137", 0]

    for node_id, node in nodes_by_id.items():
        node_type = node.get("type")
        widgets = node.get("widgets_values", []) or []
        inputs = prompt_dict[node_id]["inputs"]

        if node_type == "SaveImage":
            if "filename_prefix" not in inputs:
                value = widgets[0] if len(widgets) > 0 else "ComfyUI"
                inputs["filename_prefix"] = value

        if node_type == "KSampler":
            if seed is not None:
                inputs["seed"] = seed
            elif "seed" not in inputs:
                inputs["seed"] = widgets[0] if len(widgets) > 0 else int(time.time())
            if "steps" not in inputs:
                inputs["steps"] = widgets[2] if len(widgets) > 2 else 28
            if "cfg" not in inputs:
                inputs["cfg"] = widgets[3] if len(widgets) > 3 else 7.0
            if "sampler_name" not in inputs:
                inputs["sampler_name"] = widgets[4] if len(widgets) > 4 else "euler"
            if "scheduler" not in inputs:
                inputs["scheduler"] = widgets[5] if len(widgets) > 5 else "normal"
            if "denoise" not in inputs:
                inputs["denoise"] = widgets[6] if len(widgets) > 6 else 1.0

        if node_type == "ControlNetApplyAdvanced":
            if "strength" not in inputs:
                inputs["strength"] = widgets[0] if len(widgets) > 0 else 0.5
            if "start_percent" not in inputs:
                inputs["start_percent"] = widgets[1] if len(widgets) > 1 else 0.0
            if "end_percent" not in inputs:
                inputs["end_percent"] = widgets[2] if len(widgets) > 2 else 1.0

        if node_type == "ACN_ControlNetLoaderAdvanced":
            cnet_value = "control_v11p_sd15_lineart.pth"
            try:
                if len(widgets) > 0 and isinstance(widgets[0], str) and widgets[0]:
                    candidate = os.path.basename(widgets[0])
                    if "lineart" in candidate:
                        cnet_value = candidate
            except Exception:
                pass
            inputs["cnet"] = cnet_value

        if node_type == "IPAdapterUnifiedLoaderFaceID":
            if "preset" not in inputs:
                inputs["preset"] = widgets[0] if len(widgets) > 0 else "FACEID PLUS V2"
            scale = 1.0
            try:
                from ..config import settings as _settings
                scale = float(getattr(_settings, "IPADAPTER_STRENGTH_SCALE", 1.0))
            except Exception:
                scale = 1.0
            base_lora = widgets[1] if len(widgets) > 1 else 0.4
            inputs["lora_strength"] = max(0.0, float(base_lora) * scale)
            if "provider" not in inputs:
                inputs["provider"] = widgets[2] if len(widgets) > 2 else ("CUDA" if os.environ.get("CUDA_VISIBLE_DEVICES") else "CPU")

        if node_type == "IPAdapterFaceID":
            default_values = [1.0, 1.0, "linear", "concat", 0.0, 1.0, "V only"]
            names = [
                "weight",
                "weight_faceidv2",
                "weight_type",
                "combine_embeds",
                "start_at",
                "end_at",
                "embeds_scaling",
            ]
            scale = 1.0
            try:
                from ..config import settings as _settings
                scale = float(getattr(_settings, "IPADAPTER_STRENGTH_SCALE", 1.0))
            except Exception:
                scale = 1.0
            for idx, name in enumerate(names):
                value = widgets[idx] if len(widgets) > idx else default_values[idx]
                if name in ("weight", "weight_faceidv2"):
                    try:
                        value = float(value) * scale
                    except Exception:
                        pass
                inputs[name] = value

        if node_type == "ImageToMask":
            if use_alpha_for_mask:
                inputs["channel"] = "alpha"
            else:
                if "channel" not in inputs:
                    inputs["channel"] = widgets[0] if len(widgets) > 0 else "red"

        if node_type == "ImpactGaussianBlurMask":
            if "kernel_size" not in inputs:
                inputs["kernel_size"] = widgets[0] if len(widgets) > 0 else 10
            if "sigma" not in inputs:
                inputs["sigma"] = widgets[1] if len(widgets) > 1 else 8

        if node_type == "InpaintModelConditioning":
            if "noise_mask" not in inputs:
                inputs["noise_mask"] = widgets[0] if len(widgets) > 0 else True

        if node_type == "Image Crop Face":
            if "crop_padding_factor" not in inputs:
                inputs["crop_padding_factor"] = widgets[0] if len(widgets) > 0 else 0.1
            if "cascade_xml" not in inputs:
                inputs["cascade_xml"] = widgets[1] if len(widgets) > 1 else "haarcascade_frontalface_alt2.xml"

        if node_type == "ImageHistogramMatch+":
            if "method" not in inputs:
                inputs["method"] = widgets[0] if len(widgets) > 0 else "skimage"
            if "factor" not in inputs:
                inputs["factor"] = widgets[1] if len(widgets) > 1 else 1
            if "device" not in inputs:
                inputs["device"] = widgets[2] if len(widgets) > 2 else "cpu"

        if node_type in ("PiDiNetPreprocessor", "PIDINET_Preprocessor"):
            if "safe" not in inputs:
                inputs["safe"] = "enable"

        if node_type == "Upscale Model Loader":
            if "model_name" not in inputs:
                inputs["model_name"] = widgets[0] if len(widgets) > 0 else "RealESRGAN_x2.pth"

        if node_type == "ImageUpscaleWithModelBatched":
            if "per_batch" not in inputs:
                inputs["per_batch"] = widgets[0] if len(widgets) > 0 else 1

    return prompt_dict

def upload_image_to_comfy(img: Image.Image, filename: str, server_address: str) -> str:
    """Upload an image to ComfyUI server and return the filename"""
    try:
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        buf.seek(0)
        
        files = {
            "image": (filename, buf, "image/png")
        }
        data = {
            "overwrite": "true"
        }
        
        response = requests.post(f"{server_address}/upload/image", files=files, data=data, timeout=30)
        response.raise_for_status()
        result = response.json()
        
        uploaded_name = result.get("name", filename)
        logger.info(f"Uploaded image to ComfyUI: {uploaded_name}")
        return uploaded_name
    except Exception as e:
        logger.error(f"Failed to upload image to ComfyUI: {e}")
        raise

def _add_face_alpha_channel(pil_img: Image.Image) -> Image.Image:
    """
    Detect a face on the illustration and return an RGBA image with a soft alpha mask around it.

    Important: Our ComfyUI workflow may use `ImageToMask(channel=alpha)`. That requires the
    source image to have an alpha channel. To avoid workflow crashes like:
      "index 3 is out of bounds for dimension 3 with size 3"
    this function always returns an RGBA image.

    If a face is not detected (or detection fails), we return an RGBA image with a fully
    transparent alpha channel (no mask), so the workflow can continue safely.
    """
    try:
        import numpy as np
        import cv2

        rgb = pil_img.convert("RGB")
        img_np = np.array(rgb)
        img_bgr = cv2.cvtColor(img_np, cv2.COLOR_RGB2BGR)

        x1 = y1 = x2 = y2 = None
        try:
            cascade_path = cv2.data.haarcascades + "haarcascade_frontalface_default.xml"
            cascade = cv2.CascadeClassifier(cascade_path)
            gray = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2GRAY)
            dets = cascade.detectMultiScale(gray, scaleFactor=1.1, minNeighbors=5, minSize=(48, 48))
            if len(dets) > 0:
                dets = sorted(dets, key=lambda r: r[2] * r[3], reverse=True)
                x, y, w, h = dets[0]
                x1, y1, x2, y2 = int(x), int(y), int(x + w), int(y + h)
        except Exception:
            x1 = y1 = x2 = y2 = None

        if x1 is None or y1 is None or x2 is None or y2 is None:
            rgba = rgb.convert("RGBA")
            rgba.putalpha(Image.new("L", rgba.size, 0))
            return rgba

        h, w = img_np.shape[:2]
        x1 = max(0, min(w - 1, int(x1)))
        y1 = max(0, min(h - 1, int(y1)))
        x2 = max(0, min(w, int(x2)))
        y2 = max(0, min(h, int(y2)))
        if x2 <= x1 or y2 <= y1:
            rgba = rgb.convert("RGBA")
            rgba.putalpha(Image.new("L", rgba.size, 0))
            return rgba

        bw = x2 - x1
        bh = y2 - y1
        cx = x1 + bw // 2
        cy = y1 + int(bh * 0.55)
        ax = max(1, int(bw * 0.8))
        ay = max(1, int(bh * 1.1))

        mask = np.zeros((h, w), dtype=np.uint8)
        cv2.ellipse(mask, (cx, cy), (ax, ay), 0, 0, 360, 255, -1)
        mask = cv2.GaussianBlur(mask, (0, 0), sigmaX=25, sigmaY=25)

        rgba = rgb.convert("RGBA")
        alpha = Image.fromarray(mask)
        rgba.putalpha(alpha)
        return rgba
    except Exception as e:
        logger.warning(f"Failed to create face alpha channel: {e}")
        try:
            rgba = pil_img.convert("RGBA")
            rgba.putalpha(Image.new("L", rgba.size, 0))
            return rgba
        except Exception:
            return pil_img


def _build_face_mask(pil_img: Image.Image) -> Image.Image:
    """
    Build a grayscale face mask (L) for an illustration image.

    This is used to feed ComfyUI workflows that expect an explicit mask image and use
    `ImageToMask(channel=red)`.
    """
    try:
        import numpy as np
        import cv2

        rgb = pil_img.convert("RGB")
        img_np = np.array(rgb)
        img_bgr = cv2.cvtColor(img_np, cv2.COLOR_RGB2BGR)
        gray = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2GRAY)

        h, w = img_np.shape[:2]

        x1 = y1 = x2 = y2 = None
        try:
            cascade_path = cv2.data.haarcascades + "haarcascade_frontalface_default.xml"
            cascade = cv2.CascadeClassifier(cascade_path)
            dets = cascade.detectMultiScale(gray, scaleFactor=1.1, minNeighbors=5, minSize=(48, 48))
            if len(dets) > 0:
                dets = sorted(dets, key=lambda r: r[2] * r[3], reverse=True)
                x, y, bw, bh = dets[0]
                x1, y1, x2, y2 = int(x), int(y), int(x + bw), int(y + bh)
        except Exception:
            x1 = y1 = x2 = y2 = None

        if x1 is None or y1 is None or x2 is None or y2 is None or x2 <= x1 or y2 <= y1:
            # Fallback: centered ellipse in the upper half of the page.
            cx = w // 2
            cy = int(h * 0.45)
            ax = max(1, int(w * 0.18))
            ay = max(1, int(h * 0.22))
        else:
            bw = x2 - x1
            bh = y2 - y1
            cx = x1 + bw // 2
            cy = y1 + int(bh * 0.55)
            ax = max(1, int(bw * 0.8))
            ay = max(1, int(bh * 1.1))

        mask = np.zeros((h, w), dtype=np.uint8)
        cv2.ellipse(mask, (int(cx), int(cy)), (int(ax), int(ay)), 0, 0, 360, 255, -1)

        # Blur radius proportional to image size; tuned for 850px previews and larger.
        sigma = max(8, int(min(w, h) * 0.03))
        mask = cv2.GaussianBlur(mask, (0, 0), sigmaX=sigma, sigmaY=sigma)

        return Image.fromarray(mask)
    except Exception as e:
        logger.warning(f"Failed to build face mask, falling back to PIL mask: {e}")
        try:
            from PIL import ImageDraw, ImageFilter

            w, h = pil_img.size
            mask = Image.new("L", (w, h), 0)
            draw = ImageDraw.Draw(mask)
            cx = w // 2
            cy = int(h * 0.45)
            ax = max(1, int(w * 0.18))
            ay = max(1, int(h * 0.22))
            draw.ellipse((cx - ax, cy - ay, cx + ax, cy + ay), fill=255)
            radius = max(2, int(min(w, h) * 0.03))
            return mask.filter(ImageFilter.GaussianBlur(radius=radius))
        except Exception:
            # Last resort: full mask (avoid crashing the workflow).
            return Image.new("L", pil_img.size, 255)

def queue_prompt(workflow: dict, server_address: str) -> str:
    """Queue a workflow to ComfyUI server and return prompt_id"""
    p = {"prompt": workflow}
    data = json.dumps(p).encode("utf-8")
    
    logger.debug(f"Queueing prompt to ComfyUI: {server_address}/prompt")
    req = requests.post(f"{server_address}/prompt", data=data, headers={"Content-Type": "application/json"}, timeout=30)
    req.raise_for_status()
    result = req.json()
    
    prompt_id = result.get("prompt_id")
    logger.info(f"ComfyUI prompt queued: {prompt_id}")
    return prompt_id

def get_image_result(prompt_id: str, server_address: str, timeout: int = 300) -> Image.Image:
    """Poll ComfyUI server for result and return the generated image"""
    start = time.time()
    logger.info(f"Waiting for ComfyUI result: {prompt_id}")
    
    while time.time() - start < timeout:
        try:
            history_resp = requests.get(f"{server_address}/history/{prompt_id}", timeout=10)
            history_resp.raise_for_status()
            history = history_resp.json()

            if prompt_id in history:
                prompt_data = history[prompt_id]
                status = prompt_data.get("status", {})
                
                if status.get("completed", False):
                    outputs = prompt_data.get("outputs", {})
                    
                    preferred_ids = ["140", "9"]
                    candidate_img = None
                    candidate_meta = None

                    for nid in preferred_ids:
                        node_output = outputs.get(nid)
                        if node_output and isinstance(node_output, dict):
                            images = node_output.get("images") or []
                            if images:
                                candidate_img = images[0]
                                candidate_meta = {"node_id": nid}
                                break

                    if candidate_img is None:
                        for nid, node_output in outputs.items():
                            if isinstance(node_output, dict):
                                images = node_output.get("images") or []
                                if images:
                                    candidate_img = images[0]
                                    candidate_meta = {"node_id": nid}
                                    break

                    if candidate_img is not None:
                        filename = candidate_img.get("filename")
                        subfolder = candidate_img.get("subfolder", "")
                        logger.info(
                            f"Downloading result image from node {candidate_meta.get('node_id')}: {filename}"
                        )
                        params = {
                            "filename": filename,
                            "subfolder": subfolder,
                            "type": "output",
                        }
                        img_resp = requests.get(f"{server_address}/view", params=params, timeout=30)
                        img_resp.raise_for_status()
                        return Image.open(io.BytesIO(img_resp.content))
                
                if "error" in status:
                    error_msg = status["error"]
                    logger.error(f"ComfyUI workflow error: {error_msg}")
                    raise RuntimeError(f"ComfyUI workflow failed: {error_msg}")

            time.sleep(3)
        except requests.RequestException as e:
            logger.warning(f"Request error while polling ComfyUI: {e}")
            time.sleep(3)

    raise TimeoutError(f"ComfyUI generation timeout after {timeout}s")

def run_face_transfer_comfy_api(
    child_pil: Image.Image,
    illustration_pil: Image.Image,
    prompt: str,
    negative_prompt: str = "",
    mask_pil: Optional[Image.Image] = None,
    seed: Optional[int] = None,
) -> Image.Image:
    """
    Run face transfer using ComfyUI REST API.
    """
    server_address = settings.COMFY_BASE_URL
    logger.info(f"Starting face transfer with ComfyUI: {server_address}")
    

    # Important: Do NOT rely on alpha-channel masks. In ComfyUI, `LoadImage` typically produces
    # a 3-channel IMAGE tensor; `ImageToMask(channel=alpha)` crashes with:
    #   "index 3 is out of bounds for dimension 3 with size 3"
    # So we always provide an explicit mask image and keep `ImageToMask` on a real RGB channel.
    if mask_pil is None:
        mask_pil = _build_face_mask(illustration_pil)

    illustration_prepared = illustration_pil.convert("RGB")
    child_filename = f"child_{uuid.uuid4().hex}.png"
    illustration_filename = f"illustration_{uuid.uuid4().hex}.png"
    mask_filename = None
    
    child_uploaded = upload_image_to_comfy(child_pil, child_filename, server_address)
    illustration_uploaded = upload_image_to_comfy(illustration_prepared, illustration_filename, server_address)
    # Ensure a stable 3-channel mask image so workflows that read "red" don't break.
    mask_pil_rgb = mask_pil.convert("RGB")
    mask_filename_gen = f"mask_{uuid.uuid4().hex}.png"
    mask_filename = upload_image_to_comfy(mask_pil_rgb, mask_filename_gen, server_address)
    

    logger.info(f"Sending prompt to ComfyUI - Positive: {prompt}")
    logger.info(f"Sending prompt to ComfyUI - Negative: {negative_prompt}")
    

    workflow = build_comfy_workflow(
        child_uploaded,
        illustration_uploaded,
        prompt,
        negative_prompt,
        mask_filename=mask_filename,
        # Never use alpha-channel-based masking; always use explicit mask file.
        use_alpha_for_mask=False,
        seed=seed,
    )
    

    prompt_id = queue_prompt(workflow, server_address)
    result_img = get_image_result(prompt_id, server_address, timeout=300)
    
    logger.info(f"Face transfer completed successfully")
    return result_img

def run_face_transfer_local(
    child_pil: Image.Image,
    illustration_pil: Image.Image,
    prompt: str,
    negative_prompt: str = ""
) -> Image.Image:
    """
    Run face transfer using local Python libraries (fallback/alternative).
    Uses insightface for face detection and swapping with diffusion refinement.
    """
    try:
        import insightface
        from insightface.app import FaceAnalysis
        import cv2
        import numpy as np
        try:
            providers = ['CUDAExecutionProvider', 'CPUExecutionProvider']
        except Exception:
            providers = ['CPUExecutionProvider']
        
        app = FaceAnalysis(name='buffalo_l', providers=providers)
        app.prepare(ctx_id=0, det_size=(640, 640))
        
        child_cv = cv2.cvtColor(np.array(child_pil), cv2.COLOR_RGB2BGR)
        illust_cv = cv2.cvtColor(np.array(illustration_pil), cv2.COLOR_RGB2BGR)
        
        source_faces = app.get(child_cv)
        target_faces = app.get(illust_cv)
        
        if not source_faces or not target_faces:
            print("Warning: Face not detected in one or both images")
            return illustration_pil
        
        model_candidates = []
        env_model_path = os.environ.get('INSIGHTFACE_MODEL_PATH')
        if env_model_path:
            model_candidates.append(env_model_path)
        insightface_home = os.environ.get('INSIGHTFACE_HOME')
        if insightface_home:
            model_candidates.extend([
                os.path.join(insightface_home, 'models', 'inswapper_128.onnx'),
                os.path.join(insightface_home, 'inswapper_128.onnx'),
            ])
        model_candidates.extend([
            '/models/insightface/models/inswapper_128.onnx',
            '/models/insightface/inswapper_128.onnx',
        ])

        swapper = None
        for path in model_candidates:
            if path and os.path.exists(path):
                try:
                    swapper = insightface.model_zoo.get_model(path, download=False)
                    break
                except Exception:
                    swapper = None
        if swapper is None:
            try:
                swapper = insightface.model_zoo.get_model('inswapper_128.onnx', download=False)
            except Exception:
                swapper = None
        if swapper is None:
            raise RuntimeError("inswapper_128.onnx not found locally. Place it under /models/insightface or set INSIGHTFACE_MODEL_PATH.")
        
        result = swapper.get(illust_cv, target_faces[0], source_faces[0], paste_back=True)
        
        result_pil = Image.fromarray(cv2.cvtColor(result, cv2.COLOR_BGR2RGB))
        return result_pil
        
    except Exception as e:
        print(f"Local face transfer failed: {e}")
        return illustration_pil

def run_face_transfer(
    child_pil: Image.Image,
    illustration_uri: str,
    prompt: str,
    negative_prompt: str = "",
    randomize_seed: bool = False,
) -> Image.Image:
    """
    Main entry point for face transfer.
    Loads illustration from S3, then runs face transfer.
    """
    import boto3
    from ..config import settings
    

    s3 = boto3.client(
        "s3",
        aws_access_key_id=settings.AWS_ACCESS_KEY_ID,
        aws_secret_access_key=settings.AWS_SECRET_ACCESS_KEY,
        region_name=settings.AWS_REGION_NAME,
        endpoint_url=settings.AWS_ENDPOINT_URL,
    )
    
    if illustration_uri.startswith("s3://"):
        uri_parts = illustration_uri.replace("s3://", "").split("/", 1)
        bucket = uri_parts[0]
        key = uri_parts[1] if len(uri_parts) > 1 else ""
    else:
        bucket = settings.S3_BUCKET_NAME
        key = illustration_uri
    

    load_errors = []
    candidate_keys = [key]
    if key.lower().endswith(".png"):
        candidate_keys.append(key[:-4] + ".jpg")
        candidate_keys.append(key[:-4] + ".jpeg")
    elif key.lower().endswith(".jpg") or key.lower().endswith(".jpeg"):
        base = key[: -4] if key.lower().endswith(".jpg") else key[: -5]
        candidate_keys.append(base + ".png")

    illustration_pil = None
    explicit_mask_pil = None
    for candidate in candidate_keys:
        try:
            obj = s3.get_object(Bucket=bucket, Key=candidate)
            illustration_pil = Image.open(io.BytesIO(obj["Body"].read())).convert("RGB")
            key = candidate
            break
        except Exception as e:
            load_errors.append(str(e))

    if illustration_pil is None:
        raise RuntimeError(
            f"Failed to load illustration from {illustration_uri} (tried: {candidate_keys}). Errors: {load_errors}"
        )
    

    try:
        base_name = os.path.basename(key)
        mask_candidates = []
        if base_name.lower().endswith(".png"):
            mask_candidates.append(key.replace(base_name, f"mask_{base_name}"))
        elif base_name.lower().endswith(".jpg") or base_name.lower().endswith(".jpeg"):
            root = base_name[: -4] if base_name.lower().endswith(".jpg") else base_name[: -5]
            mask_candidates.append(key.replace(base_name, f"mask_{root}.png"))
            mask_candidates.append(key.replace(base_name, f"mask_{root}.jpg"))
        for mc in mask_candidates:
            try:
                mobj = s3.get_object(Bucket=bucket, Key=mc)
                explicit_mask_pil = Image.open(io.BytesIO(mobj["Body"].read())).convert("L")
                break
            except Exception:
                explicit_mask_pil = None
    except Exception:
        explicit_mask_pil = None
    try:
        seed = random.randint(1, 2**31 - 1) if randomize_seed else None
        return run_face_transfer_comfy_api(
            child_pil,
            illustration_pil,
            prompt,
            negative_prompt,
            mask_pil=explicit_mask_pil,
            seed=seed,
        )
    except Exception as e:
        print(f"ComfyUI API failed, falling back to local face transfer: {e}")

    try:
        return run_face_transfer_local(child_pil, illustration_pil, prompt, negative_prompt)
    except Exception as e:
        print(f"Local face transfer failed: {e}")
        return illustration_pil
