#!/usr/bin/env python3
"""
Create dummy TorchScript model for testing fraud scoring

This creates a simple MLP model that:
- Takes 776 inputs (8 features + 768 embedding)
- Returns a single logit (converted to probability via sigmoid)
- Always returns ~0.15 fraud probability for testing
"""

import torch
import torch.nn as nn
import os


class DummyFraudMLP(nn.Module):
    """Simple MLP for fraud scoring testing"""

    def __init__(self, input_dim=776, hidden_dim=128):
        super().__init__()
        self.fc1 = nn.Linear(input_dim, hidden_dim)
        self.relu = nn.ReLU()
        self.fc2 = nn.Linear(hidden_dim, 1)

        # Initialize weights to produce ~0.15 probability
        with torch.no_grad():
            self.fc1.weight.fill_(0.01)
            self.fc1.bias.fill_(0.0)
            self.fc2.weight.fill_(-0.01)
            self.fc2.bias.fill_(-1.5)  # logit ~-1.5 -> prob ~0.18

    def forward(self, x):
        x = self.fc1(x)
        x = self.relu(x)
        x = self.fc2(x)
        return x


def create_dummy_model():
    """Create and save dummy TorchScript model"""

    # Create model
    model = DummyFraudMLP()
    model.eval()

    # Create dummy input for tracing
    dummy_input = torch.randn(1, 776)

    # Trace the model
    traced_model = torch.jit.trace(model, dummy_input)

    # Test the model
    with torch.no_grad():
        test_output = traced_model(dummy_input)
        test_prob = torch.sigmoid(test_output).item()
        print(f"Test output logit: {test_output.item():.4f}")
        print(f"Test output probability: {test_prob:.4f}")

    # Save the model
    os.makedirs("models", exist_ok=True)
    model_path = "models/fraud_mlp.torchscript"
    traced_model.save(model_path)

    print(f"\n✅ Dummy TorchScript model saved to: {model_path}")
    print(f"   Input dimension: 776 (8 features + 768 embedding)")
    print(f"   Output: Single logit (use sigmoid for probability)")

    # Verify saved model can be loaded
    loaded_model = torch.jit.load(model_path)
    loaded_model.eval()

    with torch.no_grad():
        verify_output = loaded_model(dummy_input)
        verify_prob = torch.sigmoid(verify_output).item()
        print(f"\n✅ Model verification:")
        print(f"   Loaded successfully")
        print(f"   Output probability: {verify_prob:.4f}")


if __name__ == "__main__":
    create_dummy_model()