"""
Query dataset for RAG evaluation.
Provides functionality for loading and managing query datasets with corpus and question-answer pairs.
"""

import os
from typing import Dict, List, Any

import pandas as pd
from torch.utils.data import Dataset


class RAGQueryDataset(Dataset):
    """
    Dataset class for RAG query evaluation.
    
    This class provides functionality to load and manage datasets containing
    corpus documents and question-answer pairs for RAG system evaluation.
    
    Attributes:
        corpus_path: Path to the corpus JSON file
        qa_path: Path to the question-answer JSON file
        dataset: Pandas DataFrame containing the question-answer data
    """

    def __init__(self, data_dir: str):
        """
        Initialize the RAG query dataset.
        
        Args:
            data_dir: Directory containing the dataset files
        """
        super().__init__()
        
        self.corpus_path = os.path.join(data_dir, "Corpus.json")
        self.qa_path = os.path.join(data_dir, "Question.json")
        self.dataset = pd.read_json(self.qa_path, lines=True, orient="records")

    def get_corpus(self) -> List[Dict[str, Any]]:
        """
        Load and format the corpus data.
        
        Returns:
            List of dictionaries containing corpus documents with title, content, and doc_id
        """
        corpus = pd.read_json(self.corpus_path, lines=True)
        corpus_list = []
        
        for i in range(len(corpus)):
            corpus_list.append({
                "title": corpus.iloc[i]["title"],
                "content": corpus.iloc[i]["context"],
                "doc_id": i,
            })
        
        return corpus_list

    def __len__(self) -> int:
        """Get the number of samples in the dataset."""
        return len(self.dataset)

    def __getitem__(self, idx: int) -> Dict[str, Any]:
        """
        Get a sample from the dataset by index.
        
        Args:
            idx: Index of the sample to retrieve
            
        Returns:
            Dictionary containing the sample data with question, answer, and other attributes
        """
        question = self.dataset.iloc[idx]["question"]
        answer = self.dataset.iloc[idx]["answer"]
        other_attrs = self.dataset.iloc[idx].drop(["answer", "question"])
        
        return {
            "id": idx,
            "question": question,
            "answer": answer,
            **other_attrs
        }


if __name__ == "__main__":
    # Example usage
    corpus_path = "tmp.json"
    qa_path = "tmp.json"
    query_dataset = RAGQueryDataset(data_dir=qa_path)
    corpus = query_dataset.get_corpus()
    print(corpus[0])
    print(query_dataset[0])
