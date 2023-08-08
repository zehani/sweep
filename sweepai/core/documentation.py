import asyncio
import re
from deeplake.core.vectorstore.deeplake_vectorstore import VectorStore
from loguru import logger
import modal
from modal import method
from tqdm import tqdm
from sweepai.core.robots import is_url_allowed
from sweepai.core.webscrape import webscrape
from sweepai.utils.config.server import DOCS_MODAL_INST_NAME, ENV, ORG_ID


stub = modal.Stub(DOCS_MODAL_INST_NAME)
image = (
    modal.Image.debian_slim()
    .pip_install("deeplake==3.6.17", "sentence-transformers")
    .pip_install("loguru", "tqdm", "bs4", "markdownify", "lxml").run_commands(
    "apt-get install -y software-properties-common",
    "apt-add-repository non-free",
    "apt-add-repository contrib",
    "apt-get update",
    "pip install playwright==1.30.0",
    "playwright install-deps chromium",
    "playwright install chromium",
    )
)
secrets = [
    modal.Secret.from_name("activeloop"),
    modal.Secret.from_name("activeloop_token"),
]
MODEL_DIR = "/root/cache/model"
BATCH_SIZE = 128
SENTENCE_TRANSFORMERS_MODEL = "thenlper/gte-base"
model_volume = modal.NetworkFileSystem.persisted(f"{ENV}-storage")
timeout = 60 * 60  # 30 minutes

@stub.cls(
    image=image,
    secrets=secrets,
    network_file_systems={MODEL_DIR: model_volume},
    gpu="T4",
    retries=modal.Retries(
        max_retries=5, backoff_coefficient=2, initial_delay=5),
    timeout=timeout,
)
class Embedding:

    def __enter__(self):
        from sentence_transformers import SentenceTransformer # pylint: disable=import-error

        self.model = SentenceTransformer(
            SENTENCE_TRANSFORMERS_MODEL, cache_folder=MODEL_DIR
        )

    @method()
    def compute(self, texts: list[str]):
        logger.info(f"Computing embeddings for {len(texts)} texts")
        vector = self.model.encode(texts, show_progress_bar=True, batch_size=BATCH_SIZE).tolist()
        try:
            logger.info(f'{len(vector)}\n{len(vector[0])}')
        except Exception as e:
            print(f'oops {e}')
            pass
        return vector

class ModalEmbeddingFunction:
    batch_size: int = 4096 # can pick a better constant later

    def __init__(self):
        pass

    def __call__(self, texts: list[str], cpu=False):
        if len(texts) == 0:
            return []
        if cpu or len(texts) < 10: 
            return CPUEmbedding.compute.call(texts) # pylint: disable=no-member
        else:
            batches = [texts[i:i + ModalEmbeddingFunction.batch_size] for i in range(0, len(texts), ModalEmbeddingFunction.batch_size)]
            batches = [batch for batch in batches if len(batch) > 0]
            logger.info([len(batch) for batch in batches])
            results = []
            for batch in tqdm(Embedding.compute.map(batches)): # pylint: disable=no-member
                results.extend(batch)

            return results

embedding_function = ModalEmbeddingFunction()

@stub.cls(
    image=image,
    secrets=secrets,
    network_file_systems={MODEL_DIR: model_volume},
    keep_warm=1,
    retries=modal.Retries(max_retries=5, backoff_coefficient=2, initial_delay=5),
    cpu=2, # this can change later
    timeout=timeout,
)
class CPUEmbedding:
    def __enter__(self):
        from sentence_transformers import SentenceTransformer # pylint: disable=import-error

        self.model = SentenceTransformer(
            SENTENCE_TRANSFORMERS_MODEL, cache_folder=MODEL_DIR
        )

    @method()
    def compute(self, texts: list[str]) -> list[list[float]]:
        logger.info(f"Computing embeddings for {len(texts)} texts")
        vector = self.model.encode(texts, show_progress_bar=True, batch_size=BATCH_SIZE).tolist()
        try:
            logger.info(f'{len(vector)}\n{len(vector[0])}')
        except Exception as e:
            logger.info(f'oops {e}')
            pass
        return vector


def chunk_string(s):
    # Split the string into sentences
    sentences = re.split(r'(?<!\w\.\w.)(?<![A-Z][a-z]\.)(?<=\.|\?)\s', s)
    
    # If there are fewer sentences than a chunk size, return the whole string as a single chunk
    if len(sentences) <= 6:
        return [s]

    chunks = []
    i = 0

    # Slide a window of 6 sentences, moving it by 4 sentences each time
    while i < len(sentences):
        chunks.append(' '.join(sentences[i:i+6]))
        i += 4
    return chunks

def remove_non_alphanumeric(url):
    # Keep only alphanumeric characters, and remove all others
    cleaned = re.sub(r'[^a-zA-Z0-9]', '', url)
    return cleaned

@stub.function(
    image=image,
    secrets=secrets,
)
def write_documentation(doc_url):
    url_allowed = is_url_allowed(doc_url, user_agent='*')
    if not url_allowed:
        logger.info(f"URL {doc_url} is not allowed")
        return False
    idx_name = remove_non_alphanumeric(doc_url)
    url_to_documents = asyncio.run(webscrape(doc_url))
    urls, document_chunks = [], []
    for url, document in url_to_documents.items():
        if len(document) == 0:
            logger.info(f"Empty document for url {url}")
        document_chunks.extend(chunk_string(document))
        urls.extend([url] * len(chunk_string(document)))
    computed_embeddings = embedding_function(document_chunks)
    # vector_store = VectorStore(
    #     path = f'hub://{ORG_ID}/{idx_name}',
    #     runtime = {"tensor_db": True},
    #     overwrite=True,
    # )
    vector_store.add(
        text = document_chunks, 
        embedding = computed_embeddings,
        metadata = [{"url": url} for url in urls]
    )
    return True

@stub.function(
    image=image,
    secrets=secrets,
    # keep_warm=2,
)
def search_vector_store(doc_url, query):
    idx_name = remove_non_alphanumeric(doc_url)
    # vector_store = VectorStore(
    #     path = f'hub://{ORG_ID}/{idx_name}',
    #     runtime = {"tensor_db": True},
    # )
    query_embedding = embedding_function(query)
    return vector_store.search(embedding = query_embedding, k = 3)['text']