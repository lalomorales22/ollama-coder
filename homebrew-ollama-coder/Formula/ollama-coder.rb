class OllamaCoder < Formula
  desc "Agentic coding assistant for Ollama - like Claude Code, but local!"
  homepage "https://github.com/lalomorales22/ollama-coder"
  url "https://files.pythonhosted.org/packages/3d/40/16e52ae18fb1c0a631a79a972275f8e77ecd986ae39ce8e08af584004d79/ollama_coder-0.2.3.tar.gz"
  sha256 "dc394520dd25f0faf507750b0ee7219fcd444055a3ff9f3c24786b4b8eedb62d"
  license "MIT"

  depends_on "python@3.11"

  def install
    # Create empty venv structure - actual install happens in post_install
    # to avoid Homebrew's dylib relocation processing
    system "python3.11", "-m", "venv", libexec
    
    # Create wrapper script that will work after post_install completes
    (bin/"ollama-coder").write <<~EOS
      #!/bin/bash
      exec "#{libexec}/bin/ollama-coder" "$@"
    EOS
  end

  def post_install
    # Install packages AFTER Homebrew's library processing is complete
    # This avoids the pydantic-core dylib relocation error
    system libexec/"bin/pip", "install", "--upgrade", "pip"
    system libexec/"bin/pip", "install", "--no-cache-dir", "ollama-coder==#{version}"
  end

  def caveats
    <<~EOS
      ollama-coder requires Ollama to be running.
      Start Ollama with: ollama serve
      
      Then run: ollama-coder
    EOS
  end

  test do
    assert_match "usage", shell_output("#{bin}/ollama-coder --help 2>&1", 0)
  end
end
