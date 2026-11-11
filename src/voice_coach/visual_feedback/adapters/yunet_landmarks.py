"""Opt-in YuNet detector + direct LiteRT face mesh, without BlazeFace.

The geometric topology is unchanged, but the full bundle has a new digest.
Existing reference catalogs therefore cannot silently score this extractor.
Native libraries are imported only inside the isolated worker's new backend.
"""
from __future__ import annotations

import hashlib
import io
import math
import time
import zipfile

from ..feature_protocol import MAPPING, PROTOCOL, strict_json
from ..lip_geometry import normalize_lips
from ..reference_distance import UnscorableTrajectory

SCHEMA = 'voice-coaching.yunet-landmark-bundle.v1'
REVISION = 'yunet-2023mar-litert-mesh-478-pnp-v1'
FILES = {'yunet.onnx', 'face_landmarks_detector.tflite', 'canonical_face_model.obj', 'LICENSE.YuNet'}
POSE_INDICES = (1, 6, 168, 10, 33, 133, 263, 362, 234, 454)
PINS = {
    'yunet.onnx': '8f2383e4dd3cfbb4553ea8718107fc0423210dc964f9f4280604804ed2552fa4',
    'face_landmarks_detector.tflite': 'c7d54204ce0448474c7f3fa9af494787c0965cbdd6f20fc72867e43046bd43d5',
    'canonical_face_model.obj': '8bac80443397e113f41a8b565ea72c59390bc031d9defab289dba7bc0c54e618',
    'LICENSE.YuNet': 'c83b8120c50ccbd4c4f96edf53141bdd566ebb8f8e9227e415326aa1b1aba958',
}


def require(condition, code='YUNET_BUNDLE_INVALID'):
    if not condition:
        raise ValueError(code)


def read_bundle(raw):
    """None identifies the historical task; malformed new bundles fail closed."""
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        names = archive.namelist()
        if 'yunet-manifest.json' not in names:
            return None
        require(len(names) == len(set(names)) and set(names) == FILES | {'yunet-manifest.json'})
        require(all(0 < f.file_size <= 8*1024**2 and f.compress_type == zipfile.ZIP_STORED
                    for f in archive.infolist()))
        manifest = strict_json(archive.read('yunet-manifest.json'))
        require(manifest == dict(schemaVersion=SCHEMA, revision=REVISION, files=PINS,
            detection='YUNET', landmarkInference='DIRECT_LITERT_NO_BLAZEFACE',
            roiScale=1.5, maximumDetectionDimension=640,
            headPosePolicy='CANONICAL_FACE_PNP_APPROXIMATE_V1',
            referenceRecalibrationRequired=True))
        files = {name: archive.read(name) for name in FILES}
        require(all(hashlib.sha256(files[name]).hexdigest() == pin for name, pin in PINS.items()))
        return files


def crop_transform(face):
    """Affine map from original image pixels to an eye-aligned square crop."""
    import numpy as np
    require(len(face) == 15 and np.isfinite(face).all(), 'YUNET_DETECTION_INVALID')
    x, y, width, height = map(float, face[:4])
    require(width > 0 and height > 0, 'YUNET_DETECTION_INVALID')
    eyes = sorted((face[4:6], face[6:8]), key=lambda p: p[0])
    dx, dy = eyes[1] - eyes[0]
    scale = math.hypot(float(dx), float(dy))
    require(scale > 1, 'YUNET_DETECTION_INVALID')
    cosine, sine = dx / scale, dy / scale
    side = 1.5 * max(width, height)
    cx, cy = x + width / 2, y + height / 2
    factor = 256 / side
    return np.array([[factor*cosine, factor*sine, 128-factor*(cosine*cx+sine*cy)],
                     [-factor*sine, factor*cosine, 128-factor*(-sine*cx+cosine*cy)]], dtype=np.float64)


def image_landmarks(crop_landmarks, transform, width, height):
    import cv2
    import numpy as np
    points = np.asarray(crop_landmarks, dtype=np.float64)
    require(points.shape == (478, 3) and np.isfinite(points).all(), 'YUNET_LANDMARK_OUTPUT_INVALID')
    inverse = cv2.invertAffineTransform(transform)
    xy = np.column_stack((points[:, :2], np.ones(478))) @ inverse.T
    xy /= (width, height)
    require(np.isfinite(xy).all(), 'YUNET_LANDMARK_OUTPUT_INVALID')
    return xy


def head_pose(points, canonical, width, height):
    """Approximate pinhole PnP QC, never an articulation score or 3-D claim."""
    import cv2
    import numpy as np
    objects = canonical[list(POSE_INDICES)] * np.array((1, -1, -1))
    pixels = points[list(POSE_INDICES)] * (width, height)
    focal = max(width, height)
    camera = np.array([[focal, 0, width/2], [0, focal, height/2], [0, 0, 1]], dtype=np.float64)
    ok, rotation, translation = cv2.solvePnP(objects, pixels, camera, np.zeros(4), flags=cv2.SOLVEPNP_ITERATIVE)
    if not ok or not np.isfinite(rotation).all() or not np.isfinite(translation).all() or translation[2,0] <= 0:
        return None
    projected, _ = cv2.projectPoints(objects, rotation, translation, camera, np.zeros(4))
    eye_scale = float(np.linalg.norm((points[33] - points[263]) * (width, height)))
    error = float(np.sqrt(np.mean((projected.reshape(-1,2) - pixels)**2)))
    if eye_scale <= 0 or error / eye_scale > .15:
        return None
    r, _ = cv2.Rodrigues(rotation)
    yaw = math.degrees(math.atan2(-r[2,0], math.hypot(r[0,0], r[1,0])))
    pitch = math.degrees(math.atan2(r[2,1], r[2,2]))
    return yaw, pitch


class YuNetMesh:
    def __init__(self, files, quality):
        import cv2
        import numpy as np
        from ai_edge_litert.interpreter import Interpreter
        from importlib.metadata import version
        require(cv2.__version__ == '4.12.0' and np.__version__ == '2.2.6'
                and version('ai-edge-litert') == '2.3.0', 'YUNET_RUNTIME_VERSION_MISMATCH')
        self.quality = quality
        cv2.setNumThreads(1)
        self.detector = cv2.FaceDetectorYN.create('onnx',
            np.frombuffer(files['yunet.onnx'], dtype=np.uint8), np.empty(0,dtype=np.uint8),
            (320,320), quality['detectionConfidence'], .3, 5000)
        self.mesh = Interpreter(model_content=files['face_landmarks_detector.tflite'], num_threads=1)
        self.mesh.allocate_tensors()
        inputs, outputs = self.mesh.get_input_details(), self.mesh.get_output_details()
        require(len(inputs) == 1 and inputs[0]['shape'].tolist() == [1,256,256,3]
                and inputs[0]['dtype'] == np.float32, 'YUNET_LANDMARK_MODEL_INVALID')
        require(len(outputs) == 3 and outputs[0]['shape'].tolist() == [1,1,1,1434]
                and outputs[1]['shape'].tolist() == [1,1,1,1], 'YUNET_LANDMARK_MODEL_INVALID')
        self.input = inputs[0]['index']
        self.coordinates, self.presence = outputs[0]['index'], outputs[1]['index']
        self.canonical = np.array([[float(x) for x in line.split()[1:4]]
            for line in files['canonical_face_model.obj'].decode('ascii').splitlines()
            if line.startswith('v ')], dtype=np.float64)
        require(self.canonical.shape == (468,3), 'YUNET_POSE_MODEL_INVALID')

    def observe(self, rgb, pts):
        import cv2
        import numpy as np
        height, width = rgb.shape[:2]
        row = dict(pts=pts, reason=None, landmarks=[], features=[])
        ratio = min(1., 640/max(width,height))
        detection = cv2.resize(rgb, (max(16,round(width*ratio)),max(16,round(height*ratio))))
        detection = cv2.cvtColor(detection, cv2.COLOR_RGB2BGR)
        self.detector.setInputSize((detection.shape[1],detection.shape[0]))
        _, faces = self.detector.detect(detection)
        if faces is None or len(faces) != 1:
            row['reason'] = 'NO_FACE' if faces is None or len(faces) == 0 else 'MULTIPLE_FACES'
            return row
        face = faces[0].astype(np.float64).copy()
        face[:14:2] *= width/detection.shape[1]
        face[1:14:2] *= height/detection.shape[0]
        # bbox width/height occupy indices 2/3; keypoints start at 4.
        transform = crop_transform(face)
        crop = cv2.warpAffine(rgb, transform, (256,256), flags=cv2.INTER_LINEAR,
                             borderMode=cv2.BORDER_REPLICATE)
        self.mesh.set_tensor(self.input, crop[None].astype(np.float32)/255.)
        self.mesh.invoke()
        logit = float(self.mesh.get_tensor(self.presence).reshape(-1)[0])
        require(math.isfinite(logit), 'YUNET_LANDMARK_OUTPUT_INVALID')
        presence = 1 / (1 + math.exp(-max(-80.,min(80.,logit))))
        if presence < self.quality['presenceConfidence']:
            row['reason'] = 'LOW_VISIBILITY'
            return row
        points = image_landmarks(self.mesh.get_tensor(self.coordinates).reshape(478,3), transform, width, height)
        pose = head_pose(points, self.canonical, width, height)
        if pose is None or abs(pose[0]) > self.quality['maximumYawDegrees'] or abs(pose[1]) > self.quality['maximumPitchDegrees']:
            row['reason'] = 'HEAD_POSE_UNSUPPORTED'
            return row
        try:
            landmarks, features = normalize_lips(tuple(map(tuple, points)), width=width, height=height,
                mapping=MAPPING, minimum_face_scale_px=self.quality['minimumFaceScalePixels'])
            row.update(landmarks=landmarks, features=features)
        except UnscorableTrajectory:
            row['reason'] = 'LOW_VISIBILITY'
        return row


def extract_frames(files, header, read, *, timings=None):
    import numpy as np
    timings = {} if timings is None else timings
    started = time.monotonic()
    detector = YuNetMesh(files, header['quality'])
    timings['modelInitializationSeconds'] = time.monotonic() - started
    timings.update(rgbReadSeconds=0., frameInferenceSeconds=0., frameCount=0)
    width, height = header['width'], header['height']
    rows = []
    for pts in header['pts']:
        started = time.monotonic()
        rgb = np.frombuffer(read(width*height*3), dtype=np.uint8).reshape(height,width,3)
        timings['rgbReadSeconds'] += time.monotonic() - started
        started = time.monotonic()
        rows.append(detector.observe(rgb, pts))
        timings['frameInferenceSeconds'] += time.monotonic() - started
        timings['frameCount'] += 1
    return dict(protocol=PROTOCOL, mappingRevision=MAPPING.revision, frames=rows)
