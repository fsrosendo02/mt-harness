from abc import ABC, abstractmethod


class LLMProvider(ABC):
    @abstractmethod
    def generate(
        self,
        *,
        system_prompt: str | None,
        user_prompt: str,
        temperature: float = 0.2,
    ) -> str:
        """
        Return the raw text response from the model.
        """
        raise NotImplementedError
