class Agentbreeder < Formula
  include Language::Python::Virtualenv

  desc "Define Once. Deploy Anywhere. Govern Automatically — CLI for AgentBreeder"
  homepage "https://agentbreeder.io"
  url "https://files.pythonhosted.org/packages/a8/ed/ed49ee730035fe26fa1c31931ed1b97a53b80f84f0203291bf6422cba475/agentbreeder-0.1.0.tar.gz"
  sha256 "6d59b0dad0306123552625beb79f6711cfc0a3362844e4d2aa2df4a8e1780737"
  license "Apache-2.0"
  head "https://github.com/rajitsaha/agentbreeder.git", branch: "main"

  bottle do
    # Bottles generated automatically via CI — do not edit manually.
  end

  depends_on "python@3.11"

  # Core SDK (pulled as a dep by agentbreeder, listed here so Homebrew
  # resolves it within the virtualenv without hitting PyPI recursively).
  resource "agentbreeder-sdk" do
    url "https://files.pythonhosted.org/packages/f4/b6/dac966a97ff3db6923ab6d5650a8d9cdf464bafcf56e6d2c3d68e52a7810/agentbreeder_sdk-0.1.0.tar.gz"
    sha256 "ddec3330e68c62e16e54df44caa66551c01a2ad448861ead7365bfe5fce907d3"
  end

  resource "PyYAML" do
    url "https://files.pythonhosted.org/packages/source/P/PyYAML/PyYAML-6.0.2.tar.gz"
    sha256 "d584d9ec91ad65861cc08d42e834324ef890a082e591037abe114850ff7bbc3e"
  end

  def install
    virtualenv_install_with_resources
  end

  test do
    assert_match "AgentBreeder", shell_output("#{bin}/agentbreeder --help")
    assert_match version.to_s, shell_output("#{bin}/agentbreeder --version")
  end
end
