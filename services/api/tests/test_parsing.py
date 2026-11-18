import pytest

from app.clinical.bmi import bmi, bmi_category
from app.medicine.parse import parse_composition, parse_pack
from app.ocr.abbreviations import parse_duration_days, parse_frequency, parse_timing


@pytest.mark.parametrize("name,key,form,release", [
    ("Ibuprofen 400mg and Paracetamol 325mg Tablets IP", "ibuprofen:400mg+paracetamol:325mg", "TAB", "IR"),
    ("Etoricoxib Tablets IP 120 mg", "etoricoxib:120mg", "TAB", "IR"),
    ("Aspirin Gastro-resistant Tablets IP 150 mg", "aspirin:150mg", "TAB", "DR"),
    ("Metformin Hydrochloride Sustained Release Tablets IP 500 mg", "metformin:500mg", "TAB", "SR"),
    ("Amoxycillin 500mg + Clavulanic Acid 125mg Tablets", "amoxicillin:500mg+clavulanic acid:125mg", "TAB", "IR"),
    ("Paracetamol Oral Suspension IP 250mg/5ml", "paracetamol:250mg/5ml", "SUSP", "IR"),
    ("Pregabalin Capsules IP 75 mg", "pregabalin:75mg", "CAP", "IR"),
])
def test_composition(name, key, form, release):
    c = parse_composition(name)
    assert (c.key, c.form, c.release) == (key, form, release)


def test_combination_order_independent():
    a = parse_composition("Paracetamol 325mg + Ibuprofen 400mg Tablets")
    b = parse_composition("Ibuprofen 400mg and Paracetamol 325mg Tablets IP")
    assert a.key == b.key


def test_pack():
    assert parse_pack("10's") == (10.0, "units")
    assert parse_pack("60 ml") == (60.0, "ml")


@pytest.mark.parametrize("text,per_day", [("1-0-1", 2), ("1-1-1", 3), ("0-0-1", 1), ("½-0-½", 1),
                                          ("BD", 2), ("TDS", 3), ("OD", 1), ("दिन में दो बार", 2)])
def test_frequency(text, per_day):
    assert parse_frequency(text)["per_day"] == per_day


def test_sos_has_no_fixed_daily_count():
    assert parse_frequency("SOS for fever")["per_day"] is None


def test_word_on_is_not_a_frequency():
    assert parse_frequency("take on empty stomach") is None


@pytest.mark.parametrize("text,timing", [("AC", "before_food"), ("PC", "after_food"),
                                         ("खाने के बाद", "after_food"), ("khali pet", "before_food")])
def test_timing(text, timing):
    assert parse_timing(text) == timing


@pytest.mark.parametrize("text,days", [("x 5 days", 5), ("5d", 5), ("for 2 weeks", 14), ("1 month", 30),
                                       ("5 दिन", 5)])
def test_duration(text, days):
    assert parse_duration_days(text) == days


def test_bmi_asian_cutoffs():
    assert bmi(70, 170) == 24.2
    assert bmi_category(24.2) == "overweight"  # would be "normal" under WHO global cut-offs
    assert bmi_category(25.0) == "obese"
    assert bmi_category(22.9) == "normal"


@pytest.mark.parametrize("name,key", [
    ("Co-trimoxazole (Sulphamethoxazole 800mg and Trimethoprim 160mg) Tablets IP", "sulfamethoxazole:800mg+trimethoprim:160mg"),
    ("Lopinavir and Ritonavir Tablets IP (200mg + 50mg)", "lopinavir:200mg+ritonavir:50mg"),
    ("Atorvastatin (10mg) + Aspirin (75mg) + Ramipril (5mg) Capsule", "aspirin:75mg+atorvastatin:10mg+ramipril:5mg"),
    ("Ketorolac (0.5% W/V) and Moxifloxacin (0.5% W/V) Eye Drops", "ketorolac:0.5%w/v+moxifloxacin:0.5%w/v"),
    ("Calcium Acetate Tablets 667mg", "calcium acetate:667mg"),
    ("Azithromycin Oral Suspension IP 100mg per 5ml", "azithromycin:100mg/5ml"),
    ("Lignocaine Hydrochloride Injection IP 2% w/v", "lidocaine:2%w/v"),
])
def test_real_janaushadhi_names(name, key):
    assert parse_composition(name).key == key


def test_banned_list_parser_statuses():
    from app.pipelines.drugs import parse_banned_list
    text = ("1. \nPhenacetin. \nGSR NO. 578(E) Dated 23.07.1983 \n"
            "2. \n*** \nAceclofenac + Paracetamol + Rabeprazole *** \nS.O. 705 (E) Dated 10.03.2016 \n"
            "3. \nCetirizine Dihydrochloride +Diethyl Carbamazine \nS.O. 792 (E) Dated 10.03.2016 \n"
            "4.   Fixed Dose Combinations Of \nNimesulide + Levocetrizine \nS.O.1851 (E) \nDATED \n08.06.2017 \n"
            "\n*Presently stayed by the Hon'ble High Court of Madras.")
    e = parse_banned_list(text)
    assert [x["sr"] for x in e] == [1, 2, 3, 4]
    assert (e[0]["kind"], e[0]["status"]) == ("single", "in_force")
    assert e[1]["status"] == e[2]["status"] == "quashed_2016"  # whole S.O. 705–1048 series
    assert e[3]["text"] == "Nimesulide + Levocetrizine" and e[3]["status"] == "in_force"


@pytest.mark.parametrize("text,per_day", [("twice a day", 2), ("5 ml - Once a day X 1 Month", 1), ("thrice daily", 3),
                                          ("one tablet daily", 1), ("at bedtime", 1)])
def test_english_frequency(text, per_day):
    assert parse_frequency(text)["per_day"] == per_day


def test_weekly_has_no_daily_count():
    assert parse_frequency("once a week x 8 weeks")["raw"] == "weekly"


def test_transcript_parser_real_model_output():
    from app.ocr.transcript import parse_transcript
    # verbatim Qwen3-VL-4B transcription of a real handwritten Rx (MIRAGE 8BkPCKTK)
    t = ("1. Tab. coccin 2 tabs; 505\n2. Tab. cefin 500 mg 1, 80 for tender 1-1\n"
         "3. Tab. cefin 500 mg 1, 80 for tender 1-1\n4. Cap. Bifidus 1, 180\n5. Tab. ondrem-m.d. 2, 505 (tornausea)\n"
         "MED: Tab. Montair FX 1 OD\nMED: Syrup Allegra: Fexofenadine(30 mg) 5 ml - Twice a day X 4 Days\n"
         "MED: Cap Uprise D3 60K weekly\nMED: Advice plenty of fluids\nTESTS: CBC, NS1 antigen\nDIAGNOSIS: Viral fever")
    r = parse_transcript(t)
    names = [m["name"] for m in r["medicines"]]
    assert names == ["coccin", "cefin 500 mg", "Bifidus", "ondrem-m.d", "Montair FX", "Allegra", "Uprise D3 60K"]
    assert r["medicines"][0]["line"].endswith("SOS")  # '505' misread folded back to SOS
    assert r["tests"] == ["CBC", "NS1 antigen"] and r["diagnosis"] == ["Viral fever"]


def test_bracketed_flag_is_not_the_unit():
    from app.clinical.labs import parse_lab_line
    r = parse_lab_line("Haemoglobin 9.10 [L] g/dl 13.0-17.0")
    assert r.unit == "g/dl" and r.value == 9.1 and r.flag == "L"
