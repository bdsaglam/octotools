import concurrent.futures
import os
import json
import argparse
import tqdm

from pydantic import BaseModel
from octotools.engine.openai import ChatOpenAI
from octotools.settings import get_settings

from tasks.utils import ResultAnalyzer

class HallusionVDAnswerVerification(BaseModel):
    final_answer: bool


class ResultScorer:
    def __init__(self, llm_engine=None):
        self.llm_engine = llm_engine or ChatOpenAI(model_string=get_settings().default_scoring_llm, is_multimodal=False, enable_cache=True)
        print(f"\nLocal OpenAI engine {self.llm_engine.model_string} initialized.\n")

    def answer_verification(self, question, response, correct_answer):
        query_prompt = f"""
        Extract the final True/False answer from this response.
        Question: {question}
        Model response: {response}

        Return only the True/False answer from the response, ignoring all explanations.
        """

        response = self.llm_engine(query_prompt, response_format=HallusionVDAnswerVerification)
        try:
            prediction = response.final_answer
            is_correct = prediction == bool(correct_answer)
            return prediction, is_correct
        except:
            print(f"Error: {response}")
            return None, False


    def score_results(self, results, max_workers=10):
        correct = 0
        
        def process_single_result(pid_data):
            pid, question_data = pid_data
            query = question_data["query"]
            response = question_data["response"]
            correct_answer = question_data["correct_answer"]
            analysis, true_false = self.answer_verification(query, response, correct_answer)
            return pid, analysis, true_false
        
        with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = [executor.submit(process_single_result, (pid, data)) 
                      for pid, data in results.items()]
            
            for future in tqdm.tqdm(concurrent.futures.as_completed(futures), 
                                  total=len(futures), 
                                  desc="Scoring results"):
                pid, analysis, true_false = future.result()
                correct += 1 if true_false else 0
                results[pid].update({
                    "stepwise_analysis": analysis,
                    "true_false": true_false
                })
        
        return results, correct
    

def load_data(data_file, result_dir, response_type):
    # Load the benchmark data
    with open(data_file, 'r') as f:
        # convert the benchmark data to a dictionary
        benchmark_data = {data["pid"]: data for data in json.load(f)}
    
    # Load the results
    results = {}
    for file in os.listdir(result_dir):
        if file.endswith(".json") and "output_" in file:
            with open(os.path.join(result_dir, file), 'r') as f:
                result = json.load(f)
            
            # Get the index of the result
            index = file.replace(".json", "").replace("output_", "") # "0", "1", "2", ...
            pid = int(index) # NOTE adjust the index to match the pid
            assert result["pid"] == benchmark_data[pid]["pid"]

            # Save the results
            results[pid] = benchmark_data[pid]
            assert response_type in result
            results[pid]["response"] = result[response_type]
            results[pid]["correct_answer"] = benchmark_data[pid]["answer"]

    return results


def parse_args():
    parser = argparse.ArgumentParser(description="Extract and score the results from the benchmark data")
    parser.add_argument("--data_file", type=str, default="data/val.json", help="The file containing the benchmark data")
    parser.add_argument("--result_dir", type=str, default=None, help="The directory containing the results")
    parser.add_argument("--output_file", type=str, default="final_results.json", help="The file to save the extracted results")
    parser.add_argument("--log_dir", type=str, default=None, help="The directory containing the logs")
    parser.add_argument("--response_type", type=str, default="direct_output", 
                        choices=["final_output", "direct_output", "base_response"],
                        help="The type of response to extract from the results")
    parser.add_argument("--max_workers", type=int, default=16, help="The maximum number of workers to use")
    return parser.parse_args()

if __name__ == "__main__":

    args = parse_args()

    # Load and print the arguments
    print("#"*50)
    print(f"Arguments: {args}")
    for arg, value in args.__dict__.items():
        print(f"# {arg}: {value}")
    print("#"*50)

    scorer = ResultScorer()
    analyzer = ResultAnalyzer()

    # Load the results
    results = load_data(args.data_file, args.result_dir, args.response_type)

    # Score the results
    results, correct = scorer.score_results(results, max_workers=args.max_workers)

    # Calculate accuracy and wrong answers
    acc = round(correct / len(results) * 100, 2)
    print(f"\nAccuracy: {acc}% ({correct}/{len(results)})")

    # Save detailed results
    output_file = os.path.join(args.result_dir, args.output_file)
    with open(output_file, 'w') as f:
        json.dump(results, f, indent=4)
        print(f"\nResults saved to {output_file}")

    # Calculate wrong answers
    wrong_pids = [pid for pid, data in results.items() if not data["true_false"]]
    wrong_pids = sorted(wrong_pids, key=lambda x: int(x))
    wrong_indices = [int(pid) for pid in wrong_pids]
    print(f"Wrong PIDs: {wrong_pids}")
    print(f"Wrong Indices: {wrong_indices}")

    scores = {
        "correct": correct,
        "total": len(results),
        "accuracy": acc,
        "wrong_pids": wrong_pids,
        "wrong_indices": wrong_indices
    }

    # Calculate additional statistics if log directory is provided
    log_dir = args.log_dir or args.result_dir.replace("results", "logs")
    if os.path.exists(log_dir):

        if args.response_type == "base_response":
            print("Base response is not supported for scoring.")
            print("Exited.\n")
            exit()

         # Calculate the average time and steps
        step_stats = analyzer.calculate_time_steps(log_dir)
        print(f"\nStep stats:")
        for key, value in step_stats.items():
            print(f"- {key}: \t{value}")

        # Calculate the usage of tools 
        tool_usage = analyzer.calculate_tool_usage(args.result_dir)
        print(f"\nTool usage:")
        for tool, ratio in tool_usage.items():
            print(f"- {tool}: \t{ratio}")

        # Update the scores 
        scores.update({
            "step_stats": step_stats,
            "tool_usage": tool_usage
        })
    

    # Save the scores
    score_file = os.path.join(args.result_dir, f"final_scores_{args.response_type}.json")
    with open(score_file, 'w') as f:
        json.dump(scores, f, indent=4)
        print(f"Scores saved to {score_file}")
