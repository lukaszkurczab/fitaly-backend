from typing import Any, Sequence

class TfidfVectorizer:
    def __init__(
        self,
        *,
        lowercase: bool = ...,
        ngram_range: tuple[int, int] = ...,
        min_df: int = ...,
    ) -> None: ...
    def fit_transform(self, raw_documents: Sequence[str]) -> Any: ...
    def transform(self, raw_documents: Sequence[str]) -> Any: ...
