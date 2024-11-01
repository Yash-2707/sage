import json
import logging
import os
import time
from typing import List, Dict

import configargparse
from dotenv import load_dotenv
from ir_measures import MAP, MRR, P, Qrel, R, Rprec, ScoredDoc, calc_aggregate, iter_calc, nDCG

import sage.config
from sage.data_manager import GitHubRepoManager
from sage.retriever import build_retriever_from_args

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger()
logger.setLevel(logging.INFO)

load_dotenv()


def main():
    parser = configargparse.ArgParser(
        description="Runs retrieval on a benchmark dataset.", ignore_unknown_config_file_keys=True
    )
    parser.add("--benchmark", required=True, help="Path to the benchmark dataset.")
    parser.add(
        "--gold-field", default="context_files", help="Field in the benchmark dataset that contains the golden answers."
    )
    parser.add(
        "--question-field", default="question", help="Field in the benchmark dataset that contains the questions."
    )
    parser.add(
        "--logs-dir",
        default=None,
        help="Path where to output predictions and metrics. Optional, since metrics are also printed to console.",
    )

    parser.add("--max-instances", default=None, type=int, help="Maximum number of instances to process.")

    validator = sage.config.add_all_args(parser)
    args = parser.parse_args()
    validator(args)

    repo_manager = GitHubRepoManager.from_args(args)
    retriever = build_retriever_from_args(args, repo_manager)

    try:
        with open(args.benchmark, "r") as f:
            benchmark = json.load(f)
    except json.JSONDecodeError as e:
        logger.error(f"Error parsing JSON: {e}")
        return

    if args.max_instances is not None:
        benchmark = benchmark[: args.max_instances]

    golden_docs: List[Qrel] = []
    retrieved_docs: List[ScoredDoc] = []

    for question_idx, item in enumerate(benchmark):
        logger.info(f"Processing question {question_idx}...")
        query_id = str(question_idx)

        for golden_filepath in item[args.gold_field]:
            golden_docs.append(Qrel(query_id=query_id, doc_id=golden_filepath, relevance=1))

        try:
            retrieved = retriever.invoke(item[args.question_field])
            item["retrieved"] = []
            for doc_idx, doc in enumerate(retrieved):
                score = doc.metadata.get("score", doc.metadata.get("relevance_score", 1 / (doc_idx + 1)))
                retrieved_docs.append(ScoredDoc(query_id=query_id, doc_id=doc.metadata["file_path"], score=score))
                item["retrieved"].append({"file_path": doc.metadata["file_path"], "score": score})
        except Exception as e:
            logger.error(f"Error during retrieval for question {question_idx}: {e}")

    logger.info("Calculating metrics...")
    try:
        metrics = calc_aggregate([Rprec, P @ 1, R @ 3, nDCG @ 3, MAP, MRR], golden_docs, retrieved_docs)
        per_query_metrics = list(iter_calc([Rprec, P @ 1, R @ 3, nDCG @ 3, MAP, MRR], golden_docs, retrieved_docs))
    except Exception as e:
        logger.error(f"Error calculating metrics: {e}")
        return

    metrics = {str(key): value for key, value in metrics.items()}

    if args.logs_dir:
        if not os.path.exists(args.logs_dir):
            os.makedirs(args.logs_dir)

        out_data: Dict[str, any] = {
            "data": benchmark,
            "metrics": metrics,
            "per_query_metrics": per_query_metrics,
            "flags": vars(args),  # For reproducibility.
        }

        output_file = os.path.join(args.logs_dir, f"{time.time()}.json")
        with open(output_file, "w") as f:
            json.dump(out_data, f, indent=4)

    logger.info("Metrics:")
    for key in sorted(metrics.keys()):
        logger.info(f"{key}: {metrics[key]}")

    logger.info("Per-query metrics:")
    for metric in per_query_metrics:
        logger.info(f"Query {metric.query_id}: {metric.measure} - {metric.value}")

    logger.info(f"Predictions and metrics saved to {output_file}")


if __name__ == "__main__":
    main()
