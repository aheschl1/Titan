from abc import ABC, abstractmethod
from logging import warning
import torch
import torch.nn as nn
from typing import List, Tuple
from torch.optim import Optimizer
from torchviz import make_dot


class MemoryModule(ABC, nn.Module):
    def __init__(self, dim_in, dim_out, lr):
        """
        Memory module for the neural memory.
        """
        super(MemoryModule, self).__init__()
        self.lr = torch.tensor(lr)
        self.dim_in = dim_in
        self.dim_out = dim_out
        self._construct_layers()
        
    @abstractmethod
    def construct_layers(self) -> List[Tuple[str, torch.Tensor]]:
        """
        Create the weights for the model. This is an abstract method that should be implemented by the subclass.
        
        Returns:
        - List of tuples of the form (name, weight) where name is the name of the weight and weight is the tensor.
        """
        ...
    
    @abstractmethod
    def forward(self, x: torch.Tensor)->torch.Tensor:
        ...
        
    def _construct_layers(self):
        """
        Create the buffers for the model. Also create the surprise buffers. One for each weight.
        """
        weights = self.construct_layers()
        for name, weight in weights:
            self.register_buffer(name, weight)
            self.register_buffer(MemoryModule._get_corresponding_surprise_name(name), torch.zeros_like(weight, requires_grad=False))
            
    def _update_memory(self, grads, eta, alpha):
        """
        Optimizer for the neural memory.
        
        Updates based on the gradient of the surprise metric.
        
        M_t = (1-alpha)*M_{t-1} + s_t
        s_t = eta*s_{t-1} + lr*grad
        
        Args:
        - grads: the gradient of the surprise metric
        - eta: data dependent momentum
        - alpha: decay factor
        """
        if len(grads) != len(self.get_named_weights()):
            raise ValueError(f"Number of gradients {len(grads)} does not match number of weights {len(self.get_named_weights())}")
        
        for grad, (name, weight) in zip(grads, self.get_named_weights()):
            sname = MemoryModule._get_corresponding_surprise_name(name)
            # get the past surprise for this weight
            past_surprise = self.get_buffer(sname)
            if grad is None:
                warning(f"Gradient for weight is None. Skipping update.")
                continue
            surpise = eta*past_surprise + self.lr*grad # surprise_t = eta*surprise_{t-1} + lr*grad
            self.register_buffer(sname, surpise)
            self.register_buffer(name, (1-alpha)*weight + surpise)
    
    def update(self, grads, eta, alpha):
        """
        Update the memory with the gradients
        
        Args:
        - grads: the gradients
        - eta: data dependent momentum
        - alpha: decay factor
        """
        self._update_memory(grads, eta, alpha)
    
    @staticmethod
    def _get_corresponding_surprise_name(name):
        """
        Get the name of the surprise buffer corresponding to a weight name.
        """
        return f"{name}_surprise_tpast"
        
    def get_named_weights(self) -> List[Tuple[str, torch.Tensor]]:
        return [(name, weight) for name, weight in self.named_buffers() if "surprise" not in name]
    
    def get_weights(self) -> List[torch.Tensor]:
        return [weight for _, weight in self.get_named_weights()]
    
    def __call__(self, *args, **kwds):
        return self.forward(*args, **kwds)
        
class LinearMemory(MemoryModule):
    """
    A linear memory module, which is a simple matrix multiplication. No bias. No activation.
    """
    def construct_layers(self) -> List[Tuple[str, torch.Tensor]]:
        w = torch.empty(self.dim_out, self.dim_in, requires_grad=True, dtype=torch.float32)
        nn.init.xavier_normal_(w)
        return [("model", w)]
        
    def forward(self, x: torch.Tensor)->torch.Tensor:
        """
        Compute the forward pass of the model.
        
        Args:
        - x: input tensor of shape (batch_size, dim_in)
        
        Returns:
        - y: output tensor of shape (batch_size, dim_out)
        """
        return (self.model@x.permute(1,0)).permute(1,0)
    

class NeuralMemory(nn.Module):
    def __init__(self, 
                dim_in: int, 
                dim_out: int, 
                lr: float=1e-3
            ):
        super(NeuralMemory, self).__init__()
        self.memory = LinearMemory(dim_in, dim_out, lr)
        self.key = nn.Linear(dim_in, dim_in, bias=True)
        self.value = nn.Linear(dim_in, dim_in, bias=True)
        self.query = nn.Linear(dim_in, dim_in, bias=True)
        
        self.surprise_metric = nn.L1Loss(reduction='sum')
           
    def condition(self, x) -> torch.Tensor:
        """
        Condition the model on the input x
        
        Returns:
        - surprise: the surprise metric
        """
        # prepare the grad. inner loop only updates the model
        k = self.key(x)
        v = self.value(x)
        
        s_t = self.surprise_metric(self.memory(k), v) # L1Loss
        # Compute gradients w.r.t. model params
        grads = torch.autograd.grad(s_t, self.memory.get_weights(), create_graph=True, retain_graph=True)
        self.memory.update(grads, eta=torch.tensor(0.9), alpha=torch.tensor(0.1))
        return s_t

    def forward(self, x):
        return self.memory(self.query(x))
    
if __name__ == "__main__":
    x = torch.randn(12, 10, device="cuda") # tokens 1 x 10
    model = NeuralMemory(dim_in=10, dim_out=10)
    model = model.to("cuda")
    
    model.condition(x)
    
    y = model(x) 
    loss = nn.L1Loss()(y, x)

    # loss.backward() # d(MQx - x)/dw1   
    print(model.value.weight.grad)
    print(model.key.weight.grad)  
    print(model.query.weight.grad)
    
    vis = make_dot(loss, params=dict(model.named_parameters()), show_saved=True)
    # save
    vis.render("model", format="png", cleanup=True)
