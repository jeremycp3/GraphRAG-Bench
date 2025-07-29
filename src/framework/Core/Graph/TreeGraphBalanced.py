from Core.Graph.BaseGraph import BaseGraph
from Core.Schema.ChunkSchema import TextChunk
from Core.Common.Logger import logger
from Core.Index.EmbeddingFactory import get_rag_embedding
from Core.Prompt.RaptorPrompt import SUMMARIZE
from Core.Storage.TreeGraphStorage import TreeGraphStorage
from Core.Schema.TreeSchema import TreeNode

from sklearn.metrics import pairwise_distances_argmin_min

import asyncio
from concurrent.futures import ThreadPoolExecutor, as_completed
import time

from typing import List, Set, Any

Embedding = List[float]

import numpy as np
import umap
import random
from sklearn.mixture import GaussianMixture

class TreeGraphBalanced(BaseGraph):
    max_workers: int = 16
    leaf_workers: int = 32
    def __init__(self, config, llm, encoder):
        super().__init__(config, llm, encoder)
        self._graph: TreeGraphStorage = TreeGraphStorage()  # Tree index
        self.embedding_model = get_rag_embedding(config.embedding.api_type, config)  # Embedding model
        self.config = config.graph # Only keep the graph config
        random.seed(self.config.random_seed)

    def _create_task_for(self, func):
        def _pool_func(**params):
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            loop.run_until_complete(func(**params))
            loop.close()
        return _pool_func

    def _create_task_with_return(self, func):
        def _pool_func(**params):
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            result = loop.run_until_complete(func(**params))
            loop.close()
            return result
        return _pool_func

    async def _perform_clustering(
        self, embeddings: np.ndarray
    ) -> List[np.ndarray]:

        n_samples = embeddings.shape[0]
        logger.info("Perform Clustering: n_samples = {n_samples}".format(n_samples=n_samples))
        n_clusters = n_samples // self.config.size_of_clusters
        centers = embeddings[np.random.choice(n_samples, n_clusters, replace=False)]
        labels = np.zeros(n_samples, dtype=int)
        cluster_sizes = np.zeros(n_clusters, dtype=int)
        max_size_diff = self.config.max_size_percentage * n_samples / n_clusters

        def _balance_clusters():
            for i in range(n_samples):
                if cluster_sizes[new_labels[i]] > n_samples / n_clusters + max_size_diff:
                    small_clusters = [j for j in range(n_clusters) if cluster_sizes[j] < n_samples / n_clusters]
                    distances = np.linalg.norm(embeddings[i] - new_centers[small_clusters], axis=1)
                    new_cluster = small_clusters[np.argmin(distances)]
                    cluster_sizes[new_labels[i]] -= 1
                    new_labels[i] = new_cluster
                    cluster_sizes[new_labels[i]] += 1

        for i in range(self.config.max_iter):
            logger.info("Performing balanced K-means: iteration {iter}".format(iter=i))
            new_labels = pairwise_distances_argmin_min(embeddings, centers)[0]
            
            def _process_clusters(n_clusters, new_labels):
                mapping = {v: i for i, v in enumerate(dict.fromkeys(new_labels))}
                new_labels = [mapping[x] for x in new_labels]
                n_clusters = len(mapping)
                # import pdb
                # pdb.set_trace()
                cluster_sizes = np.bincount(new_labels, minlength=n_clusters)  # Update cluster sizes
                new_centers = np.array([embeddings[np.array(new_labels) == i].mean(axis=0) for i in range(n_clusters)])
                return n_clusters, new_labels, new_centers, cluster_sizes
            
            n_clusters, new_labels, new_centers, cluster_sizes = _process_clusters(n_clusters, new_labels)
            _balance_clusters()
            n_clusters, new_labels, new_centers, cluster_sizes = _process_clusters(n_clusters, new_labels)

            if n_clusters < n_samples // self.config.size_of_clusters:
                break

            center_shift = np.linalg.norm(new_centers - centers)
            if center_shift <= self.config.tol:
                break

            centers = new_centers
            labels = new_labels

        return labels


    async def _clustering(self, nodes: List[TreeNode]) -> List[List[TreeNode]]:
        # Get the embeddings from the nodes
        embeddings = np.array([node.embedding for node in nodes])
        # Perform the clustering
        clusters = await self._perform_clustering(embeddings)
        unique_values, inverse_indices = np.unique(clusters, return_inverse=True)
        sorted_indices = np.argsort(inverse_indices)
        clustered_indices = np.split(sorted_indices, np.cumsum(np.bincount(inverse_indices))[:-1])
        node_clusters = [[nodes[i] for i in cluster] for cluster in clustered_indices]

        return node_clusters

    def _embed_text(self, text: str):
        return self.embedding_model._get_text_embedding(text)

    async def _create_node(self, layer: int, text: str, children_indices: Set[int] = None):
        embedding = self._embed_text(text)
        node_id = self._graph.num_nodes  # Give it an index
        logger.info(
            "Create node_id = {node_id}, children = {children}".format(node_id=node_id, children=children_indices))
        return self._graph.upsert_node(node_id=node_id,
                                       node_data={"layer": layer, "text": text, "children": children_indices,
                                                  "embedding": embedding})

    async def _extract_entity_relationship(self, chunk_key_pair: tuple[str, TextChunk]) -> TreeNode:
        # Build a leaf node from a text chunk
        chunk_key, chunk_info = chunk_key_pair
        leaf_node = await self._create_node(0, chunk_info.content)
        return leaf_node

    async def _extract_cluster_relationship(self, layer: int, cluster: List[TreeNode]) -> TreeNode:
        # Build a non-leaf node from a cluster of nodes
        summarized_text = await self._summarize_from_cluster(cluster, self.config.summarization_length)
        parent_node = await self._create_node(layer, summarized_text, {node.index for node in cluster})
        return parent_node

    async def _create_node_without_embedding(self, layer: int, text: str, children_indices: Set[int] = None):
        # embedding = self._embed_text(text)
        logger.info(
            "Create node_id = unassigned, children = {children}".format(node_id=0, children=children_indices))
        return self._graph.upsert_node(node_id=0,
                                       node_data={"layer": layer, "text": text, "children": children_indices,
                                                  "embedding": []})

    async def _extract_entity_relationship_without_embedding(self, chunk_key_pair: tuple[str, TextChunk]) -> TreeNode:
        # Build a leaf node from a text chunk
        chunk_key, chunk_info = chunk_key_pair
        leaf_node = await self._create_node_without_embedding(0, chunk_info.content)
        return leaf_node

    async def _extract_cluster_relationship_without_embedding(self, layer: int, cluster: List[TreeNode]) -> TreeNode:
        # Build a non-leaf node from a cluster of nodes
        summarized_text = await self._summarize_from_cluster(cluster, self.config.summarization_length)
        parent_node = await self._create_node_without_embedding(layer, summarized_text, {node.index for node in cluster})
        return parent_node

    async def _summarize_from_cluster(self, node_list: List[TreeNode], summarization_length=150) -> str:
        # Give a summarization from a cluster of nodes
        node_texts = f"\n\n".join([' '.join(node.text.splitlines()) for node in node_list])
        content = SUMMARIZE.format(context=node_texts)
        return await self.llm.aask(content, max_tokens=summarization_length)

    async def _batch_embed_and_assign(self, layer):
        current_layer = self._graph.get_layer(layer)
        texts = [node.text for node in current_layer]
               # For openai embedding model 
        embeddings = []
        batch_size = self.embedding_model.embed_batch_size
        
        for i in range(0, len(texts), batch_size):
            batch = texts[i:i + batch_size]
            batch_embeddings = self.embedding_model._get_text_embeddings(batch)
            embeddings.extend(batch_embeddings)
        # embeddings = self.embedding_model._get_text_embeddings(texts)
        start_id = self._graph.get_node_num() - len(self._graph.get_layer(layer))
        for i in range(start_id, len(self._graph.nodes)):
            self._graph.nodes[i].id = i
            self._graph.nodes[i].embedding = embeddings[i - start_id]
        for node, embedding in zip(self._graph.get_layer(layer), embeddings):
            node.embeddings = embedding
            node.index = start_id
            start_id += 1

    async def _build_tree_from_leaves(self):
        for layer in range(self.config.num_layers):  # build a new layer
            logger.info("length of layer: {length}".format(length=len(self._graph.get_layer(layer))))
            if len(self._graph.get_layer(layer)) <= self.config.reduction_dimension + 1:
                break

            self._graph.add_layer()

            clusters = await self._clustering(nodes = self._graph.get_layer(layer))

            with ThreadPoolExecutor(max_workers=self.max_workers) as pool:
                for i in range(0, self.max_workers):
                    cluster_tasks = [pool.submit(self._create_task_for(self._extract_cluster_relationship_without_embedding), layer = layer + 1, cluster = cluster) for (j, cluster) in enumerate(clusters) if j % self.max_workers == i]
                    as_completed(cluster_tasks)

            logger.info("To batch embed current layer")
            await self._batch_embed_and_assign(self._graph.num_layers - 1)


            logger.info("Layer: {layer}".format(layer=layer))

        logger.info(self._graph.num_layers)
        

    async def _build_graph(self, chunks: List[Any]):
        is_load = await self._graph.load_tree_graph_from_leaves()
        if is_load:
            logger.info(f"Loaded {len(self._graph.leaf_nodes)} Leaf Embeddings")
        else:
            self._graph.clear()  # clear the storage before rebuilding
            self._graph.add_layer()
            with ThreadPoolExecutor(max_workers=self.max_workers) as pool:
                # leaf_tasks = []
                # for index, chunk in enumerate(chunks):
                #     logger.info(index)
                #     leaf_tasks.append(pool.submit(self._create_task_for(self._extract_entity_relationship), chunk_key_pair=chunk))
                for i in range(0, self.max_workers):
                    leaf_tasks = [pool.submit(self._create_task_for(self._extract_entity_relationship_without_embedding), chunk_key_pair=chunk) for index, chunk in enumerate(chunks) if index % self.max_workers == i]
                    as_completed(leaf_tasks)
                    # time.sleep(2)
            logger.info(len(chunks))
            logger.info(f"To batch embed leaves")
            await self._batch_embed_and_assign(self._graph.num_layers - 1)
            logger.info(f"Created {len(self._graph.leaf_nodes)} Leaf Embeddings")
            await self._graph.write_tree_leaves()
        await self._build_tree_from_leaves()
        
    @property
    def entity_metakey(self):
        return "index"
