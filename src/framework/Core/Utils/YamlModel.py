from pathlib import Path
from typing import Dict, Optional

import yaml
from pydantic import BaseModel, model_validator


class YamlModel(BaseModel):
    """Base class for yaml model"""

    extra_fields: Optional[Dict[str, str]] = None

    @classmethod
    def read_yaml(cls, file_path: Path, encoding: str = "utf-8") -> Dict:
        """Read yaml file and return a dict"""
        if not file_path.exists():
            return {}
        with open(file_path, "r", encoding=encoding) as file:
            return yaml.safe_load(file)

   
class YamlModelWithoutDefault(YamlModel):
    """YamlModel without default values"""

    @model_validator(mode="before")
    @classmethod
    def check_not_default_config(cls, values):
        """Check if there is any default config in merged_config.yaml"""
        if any(["YOUR" in v for v in values]):
            raise ValueError("Please set your config in merged_config.yaml")
        return values