import { Activity, Baby, Brain, Droplet, HeartPulse, Thermometer, Venus, Wind } from "lucide-react";

/** Quick symptom chips (Home + Ask): icon + translated label, English complaint seeds the symptom check. */
export const SYMPTOM_CHIPS = [
  { key: "fever", icon: Thermometer, complaint: "fever" },
  { key: "cough", icon: Wind, complaint: "cough" },
  { key: "headache", icon: Brain, complaint: "headache" },
  { key: "stomach", icon: Activity, complaint: "stomach pain" },
  { key: "sugar", icon: Droplet, complaint: "high blood sugar or diabetes symptoms" },
  { key: "bp", icon: HeartPulse, complaint: "high blood pressure symptoms" },
  { key: "child", icon: Baby, complaint: "my child is unwell" },
  { key: "women", icon: Venus, complaint: "a women's health problem" },
];
