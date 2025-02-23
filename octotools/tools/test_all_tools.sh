#! /bin/bash

find . -name "test.log" -type f -delete

# Find all tool.py files in the tools folder
# tools=$(find . -type f -name "tool.py")
tools=(
    ./text_detector/tool.py
    ./url_text_extractor/tool.py
    ./nature_news_fetcher/tool.py
    ./generalist_solution_generator/tool.py
    ./google_search/tool.py
    ./python_code_generator/tool.py
    ./relevant_patch_zoomer/tool.py
    ./pubmed_search/tool.py
    ./arxiv_paper_searcher/tool.py
    ./wikipedia_knowledge_searcher/tool.py
)

echo "Testing selected tools"

# print the tools
echo "Tools:"
for tool in "${tools[@]}"; do
    echo "  - $(basename $(dirname $tool))"
done

# Track if any tests fail
failed=0

# run the test script in each tool
for tool in "${tools[@]}"; do
    tool_dir=$(dirname $tool)
    tool_name=$(basename $tool_dir)

    echo ""
    echo "Testing $tool_name..."
    
    # Save current directory
    pushd $tool_dir > /dev/null
    
    # Run test and capture exit code
    python tool.py > test.log 2>&1
    if [ $? -ne 0 ]; then
        echo "❌ $tool_name failed! Check $tool_dir/test.log for details"
        failed=1
    else
        echo "✅ $tool_name passed"
    fi
    
    # Return to original directory
    popd > /dev/null
done

echo ""
echo "Done testing selected tools"
echo "Failed: $failed"