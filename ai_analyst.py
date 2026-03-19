import json
import constants


def build_prompt(location_name: str, readings: list) -> str:
    """Build the LLM analysis prompt from recent readings."""
    data_json = json.dumps(readings, indent=2, default=str)
    actions = constants.ACTIONS_TO_TAKE

    return f"""You are an AI system monitoring the ISS Environmental Control and Life Support System (ECLSS).
You will be given recent sensor readings from a specific module, along with anomaly detection results.

Location: {location_name}

Each reading includes:
- sensor data (parameter → value pairs)
- isolation_forest_label: -1 = anomalous, 1 = normal
- rf_classification: probability distribution over fault types (only provided when anomalous)

Recent sensor data:
{data_json}

Available remediation actions:
{json.dumps(actions, indent=2)}

Instructions:
1. Analyse the sensor trends and anomaly labels.
2. Identify the most likely fault if one is present.
3. Recommend the single most appropriate action from the list above.
4. Keep your explanation concise (3-5 sentences).
5. End your response with exactly this format on its own line:
   action: [action name here] location: [{location_name}]
"""


def analyze(location_name: str, readings: list, model: str = "mistral") -> str:
    """
    Send sensor data to a local Ollama model and return the response text.
    Returns an error string if Ollama is unavailable.
    """
    try:
        import ollama
    except ImportError:
        return "Error: ollama package not installed. Run: pip install ollama"

    if not readings:
        return "No sensor data available for analysis."

    prompt = build_prompt(location_name, readings)

    try:
        response = ollama.generate(model=model, prompt=prompt)
        return response.get("response", "No response from model.")
    except Exception as e:
        return f"Error communicating with Ollama: {e}\n\nMake sure Ollama is running: ollama serve"
