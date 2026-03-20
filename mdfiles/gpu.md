You are going to set up a CodeLlama 7B Instruct GPU inference service for our Cursor project. Follow these instructions exactly:

1. Create a new folder in the repo called `gpu_service`.
2. Inside `gpu_service`, create:
   - `generate_service.py` (FastAPI server for LLM inference)
   - `requirements.txt` with: torch, transformers, sentencepiece, accelerate, fastapi, uvicorn
3. In `generate_service.py`, load CodeLlama 7B Instruct on GPU:
   - Load tokenizer and model with device_map="auto"
   - Provide a function `generate_steps(prompt: str)` that returns the generated text
   - Expose FastAPI endpoint `/generate_steps` which accepts JSON with `prompt` and returns `{"output": <generated_text>}`
4. Keep prompts separate in a folder `prompts/` as `step_prompt_template.txt` for easy editing.
5. Replace all Azure OpenAI GPT-4 Mini calls in:
   - `app/steps/step_generation.py`
   - `app/steps/step_execution.py`
   - `app/generator/script_generator.py`
   with a function `get_steps_from_gpu(prompt: str)` that calls the local FastAPI GPU server.
6. Ensure JSON schema parsing remains exactly as before — do not remove any `response_format` or schema validation.
7. Add instructions to run server: