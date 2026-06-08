import requests
import json
from ai_prompts import (
    WRITER_PROMPT,
    CENSOR_PROMPT,
    PUBLISHER_PROMPT,
    GENERATOR_PROMPT
)

OLLAMA_URL = "http://localhost:11434/api/chat"
MODEL_NAME = "llama3"


class AIService:

    def ask_ollama(self, prompt):
        try:
            response = requests.post(
                OLLAMA_URL,
                json={
                    "model": MODEL_NAME,
                    "messages": [
                        {
                            "role": "user",
                            "content": prompt
                        }
                    ],
                    "stream": False,
                    "options": {
                        "temperature": 0.65,
                        "top_k": 40,
                        "top_p": 0.8
                    }
                },
                timeout=120
            )

            print("STATUS:", response.status_code)
            print("RAW RESPONSE:", response.text)

            if response.status_code != 200:
                return False, "Ошибка Ollama"

            data = response.json()

            print("PARSED RESPONSE", data)

            result = data.get("message", {}).get("content", "")

            if not result:
                return False, "Пустой ответ от AI"
            
            return True, result.strip()

        except Exception as e:
            return False, str(e)

    # =====================
    # WRITER ROLE
    # =====================
    def improve_text(self, text):
        prompt = f"""
        {WRITER_PROMPT}

        Исходный текст:
        {text}
        """
        return self.ask_ollama(prompt)

    def check_grammar(self, text):
        prompt = f"""
        Исправь грамматические ошибки:

        {text}
        """
        return self.ask_ollama(prompt)

    def suggest_titles(self, text):
        prompt = f"""
        Придумай 3 заголовка для текста:

        {text}
        """
        success, result = self.ask_ollama(prompt)

        if success:
            titles = result.split("\n")
            return True, titles

        return False, result
    
    def generate_post(self, topic, previous_posts_context):
        prompt = f"""
        {GENERATOR_PROMPT}

        Вот примеры успешных постов для анализа стиля:

        {previous_posts_context}

        Теперь напиши новый пост на тему: "{topic}"

        Используй тот же стиль и тон, что и в примерах.
        Пост должен быть длиной 150-300 слов.
        Добавь релевантные эмодзи и хэштеги.
        """
        return self.ask_ollama(prompt)

    # =====================
    # CENSOR ROLE
    # =====================
    def analyze_post_for_moderation(self, title, text):
        prompt = f"""
        {CENSOR_PROMPT}

        Заголовок:
        {title}

        Текст:
        {text}
        """

        success, result = self.ask_ollama(prompt)

        if success:
            try:
                json_start = result.find('{')
                json_end = result.rfind('}') + 1
                
                if json_start != -1 and json_end > json_start:
                    json_str = result[json_start:json_end]
                    parsed = json.loads(json_str)
                    return True, parsed
                else:
                    return True, json.loads(result)
            except json.JSONDecodeError as e:
                print(f"JSON parse error: {e}")
                print(f"Raw result: {result}")
                return False, f"Ошибка парсинга JSON: {str(e)}"
        
        return False, result

    # =====================
    # PUBLISHER ROLE
    # =====================
    def analyze_for_publication(self, title, text, platforms):
        prompt = f"""
        {PUBLISHER_PROMPT}

        Заголовок:
        {title}

        Текст:
        {text}

        Платформы:
        {platforms}
        """
        return self.ask_ollama(prompt)

    def optimize_for_platform(self, text, platform):
        prompt = f"""
        Оптимизируй текст для платформы {platform}

        {text}
        """
        return self.ask_ollama(prompt)

    def analyze_seo(self, text):
        prompt = f"""
        Проанализируй SEO текста:

        {text}
        """
        return self.ask_ollama(prompt)


ai_service = AIService()