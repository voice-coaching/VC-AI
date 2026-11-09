"""Extractor identity, affine coordinate recovery and approximate pose QC."""
import io
import json
import math
import unittest
import zipfile

import cv2
import numpy as np

from voice_coach.visual_feedback.adapters.yunet_landmarks import (
    FILES, PINS, SCHEMA, REVISION, POSE_INDICES, crop_transform,
    image_landmarks, head_pose, read_bundle)


class YuNetExtractor(unittest.TestCase):
    def test_historical_task_does_not_opt_into_new_backend(self):
        stream=io.BytesIO()
        with zipfile.ZipFile(stream,'w') as z:
            z.writestr('face_detector.tflite',b'historical-fixture')
        self.assertIsNone(read_bundle(stream.getvalue()))

    def test_new_bundle_requires_complete_exact_assets(self):
        manifest=dict(schemaVersion=SCHEMA,revision=REVISION,files=PINS,detection='YUNET',
            landmarkInference='DIRECT_LITERT_NO_BLAZEFACE',roiScale=1.5,
            maximumDetectionDimension=640,headPosePolicy='CANONICAL_FACE_PNP_APPROXIMATE_V1',
            referenceRecalibrationRequired=True)
        for extra in (False,True):
            stream=io.BytesIO()
            with zipfile.ZipFile(stream,'w') as z:
                z.writestr('yunet-manifest.json',json.dumps(manifest))
                for name in FILES:z.writestr(name,b'wrong-bytes')
                if extra:z.writestr('../unexpected',b'invalid')
            with self.assertRaisesRegex(ValueError,'YUNET_BUNDLE_INVALID'):
                read_bundle(stream.getvalue())

    def test_eye_aligned_crop_round_trip_preserves_non_square_coordinates(self):
        face=np.array([280,150,180,220,330,230,410,210,370,270,340,310,400,310,.9],dtype=float)
        matrix=crop_transform(face)
        points=np.tile([350.,280.],(478,1));points[13]=[345.,270.];points[14]=[350.,290.]
        cropped=np.column_stack((points,np.ones(478)))@matrix.T
        crop_landmarks=np.column_stack((cropped,np.zeros(478)))
        recovered=image_landmarks(crop_landmarks,matrix,1280,720)
        np.testing.assert_allclose(recovered,points/(1280,720),atol=1e-12)

    def test_pose_qc_recovers_known_synthetic_camera_rotation(self):
        canonical=np.zeros((468,3))
        data=((0,0,3),(0,1,2),(0,2,1),(0,5,-1),(-3,2,0),(-1.5,2,1),
              (3,2,0),(1.5,2,1),(-5,0,-2),(5,0,-2))
        canonical[list(POSE_INDICES)]=data
        yaw,pitch=math.radians(25),math.radians(10)
        ry=np.array([[math.cos(yaw),0,math.sin(yaw)],[0,1,0],[-math.sin(yaw),0,math.cos(yaw)]])
        rx=np.array([[1,0,0],[0,math.cos(pitch),-math.sin(pitch)],[0,math.sin(pitch),math.cos(pitch)]])
        rotation,_=cv2.Rodrigues(ry@rx)
        camera=np.array([[640.,0,320],[0,640,240],[0,0,1]])
        pixels,_=cv2.projectPoints(canonical[list(POSE_INDICES)]*(1,-1,-1),rotation,
                                  np.array([0.,0.,60.]),camera,np.zeros(4))
        landmarks=np.zeros((478,2));landmarks[list(POSE_INDICES)]=pixels.reshape(-1,2)/(640,480)
        measured=head_pose(landmarks,canonical,640,480)
        self.assertIsNotNone(measured)
        self.assertAlmostEqual(measured[0],25,places=5)
        self.assertAlmostEqual(measured[1],10,places=5)

    def test_degenerate_detection_and_nonfinite_mesh_are_rejected(self):
        with self.assertRaises(ValueError):crop_transform(np.zeros(15))
        points=np.zeros((478,3));points[13,0]=float('nan')
        with self.assertRaises(ValueError):image_landmarks(points,np.eye(2,3),640,480)
