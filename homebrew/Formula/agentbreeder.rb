class Agentbreeder < Formula
  include Language::Python::Virtualenv

  desc "Define Once. Deploy Anywhere. Govern Automatically."
  homepage "https://github.com/open-agent-garden/agentbreeder"
  url "https://files.pythonhosted.org/packages/source/a/agentbreeder/agentbreeder-0.1.0.tar.gz"
  sha256 "PLACEHOLDER_SHA256"
  license "Apache-2.0"

  depends_on "python@3.12"
  depends_on "libpq"

  def install
    virtualenv_install_with_resources
  end

  test do
    assert_match "agentbreeder", shell_output("#{bin}/agentbreeder --help")
  end
end
