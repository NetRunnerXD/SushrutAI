"""BMI using the Asian-Indian cut-offs (Misra et al., Consensus Statement for Diagnosis of Obesity,
Abdominal Obesity and the Metabolic Syndrome for Asian Indians, JAPI 2009; also WHO Expert
Consultation, Lancet 2004): normal 18.0–22.9, overweight 23.0–24.9, obese ≥25.
"""


def bmi(weight_kg: float, height_cm: float) -> float:
    return round(weight_kg / (height_cm / 100) ** 2, 1)


def bmi_category(value: float) -> str:
    if value < 18.0:
        return "underweight"
    if value < 23.0:
        return "normal"
    if value < 25.0:
        return "overweight"
    return "obese"
