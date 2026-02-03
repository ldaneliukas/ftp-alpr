from fast_alpr import ALPR

# You can also initialize the ALPR with custom plate detection and OCR models.
alpr = ALPR(
    detector_model="yolo-v9-t-384-license-plate-end2end",
    ocr_model="cct-xs-v1-global-model",
)

# The "gate/masina.jpg" can be found in repo root dir
alpr_results = alpr.predict("gate/masina.jpg")
print(alpr_results)
