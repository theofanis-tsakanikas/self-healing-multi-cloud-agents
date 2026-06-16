"""
Regression: the LLM intermittently emits ONE extra standalone '}' at the tail of
outputs.tf (observed 3× on Azure us_crm, always same spot). It fails `terraform init`
with 'Argument or block definition required'. _fix_terraform_stray_brace drops a '}'
that closes nothing (brace-depth 0) — and ONLY then — so valid balanced .tf is untouched.
"""
from agents.tools import _fix_terraform_stray_brace


# The exact broken outputs.tf the user pasted — a stray '}' after managed_identity_id.
_BROKEN = '''output "storage_account_name" {
  value = azurerm_storage_account.data.name
}

output "container_name" {
  value = azurerm_storage_container.data.name
}

output "managed_identity_id" {
  value = data.azurerm_user_assigned_identity.pipeline.id
}
}

output "resource_group_name" {
  value = data.azurerm_resource_group.main.name
}'''


def test_stray_brace_removed_and_balanced():
    fixed = _fix_terraform_stray_brace(_BROKEN)
    assert fixed.count("{") == fixed.count("}"), fixed
    # The five real output blocks survive; only the lone extra '}' is gone.
    assert fixed.count("output ") == 4
    assert "resource_group_name" in fixed
    assert _BROKEN.count("}") - fixed.count("}") == 1


def test_balanced_file_untouched():
    # A valid, nested main.tf must pass through byte-for-byte (no false repair).
    valid = '''resource "azurerm_storage_account" "data" {
  name = var.storage_account_name
  blob_properties {
    delete_retention_policy {
      days = 7
    }
  }
  tags = {
    project_id = var.project_id
  }
}'''
    assert _fix_terraform_stray_brace(valid) == valid


def test_interpolation_not_miscounted():
    # ${...} braces are balanced on their own line → depth tracking stays correct.
    valid = '''output "url" {
  value = "https://${azurerm_storage_account.data.name}.blob.core.windows.net"
}'''
    assert _fix_terraform_stray_brace(valid) == valid
