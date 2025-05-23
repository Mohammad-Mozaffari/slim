import torch
import torch.nn as nn

from transformers.models.opt.modeling_opt import OPTDecoderLayer
from transformers.models.bloom.modeling_bloom import BloomBlock
from transformers.models.llama.modeling_llama import LlamaDecoderLayer, LlamaRMSNorm

@torch.no_grad()
def smooth_ln_fcs(ln, fcs, act_scales, alpha=0.5):
    if not isinstance(fcs, list):
        fcs = [fcs]
    assert isinstance(ln, nn.LayerNorm) or isinstance(ln, LlamaRMSNorm) or "RMSNorm" in ln.__class__.__name__
    for fc in fcs:
        assert isinstance(fc, nn.Linear)
        assert ln.weight.numel() == fc.in_features == act_scales.numel()

    device, dtype = fcs[0].weight.device, fcs[0].weight.dtype
    act_scales = act_scales.to(device=device, dtype=dtype)
    weight_scales = torch.cat([fc.weight.abs().max(
        dim=0, keepdim=True)[0] for fc in fcs], dim=0)
    weight_scales = weight_scales.max(dim=0)[0].clamp(min=1e-5)

    scales = (act_scales.pow(alpha) / weight_scales.pow(1-alpha)
              ).clamp(min=1e-5).to(device).to(dtype)

    ln.weight.div_(scales)
    if hasattr(ln, "bias"):
        ln.bias.div_(scales)

    for fc in fcs:
        fc.weight.mul_(scales.view(1, -1))

@torch.no_grad()
def smooth_lm(model, scales, alpha=0.5):
    for name, module in model.named_modules():
        if isinstance(module, OPTDecoderLayer):
            attn_ln = module.self_attn_layer_norm
            qkv = [module.self_attn.q_proj,
                   module.self_attn.k_proj, module.self_attn.v_proj]
            qkv_input_scales = scales[name + '.self_attn.q_proj']
            smooth_ln_fcs(attn_ln, qkv, qkv_input_scales, alpha)

            ffn_ln = module.final_layer_norm
            fc1 = module.fc1
            fc1_input_scales = scales[name + '.fc1']
            smooth_ln_fcs(ffn_ln, fc1, fc1_input_scales, alpha)
        elif isinstance(module, LlamaDecoderLayer):
            attn_ln = module.input_layernorm
            qkv = [module.self_attn.q_proj,
                   module.self_attn.k_proj, module.self_attn.v_proj]
            if "layer" in name:
                qkv_input_scales = scales[name + '.self_attn.q_proj']
            else:
                qkv_input_scales = scales['self_attn.q_proj']
            smooth_ln_fcs(attn_ln, qkv, qkv_input_scales, alpha)

            ffn_ln = module.post_attention_layernorm
            fc1 = [module.mlp.gate_proj, module.mlp.up_proj]
            if "layer" in name:
                fc1_input_scales = scales[name + '.mlp.up_proj']
            else:
                fc1_input_scales = scales['mlp.up_proj']

            smooth_ln_fcs(ffn_ln, fc1, fc1_input_scales, alpha)
        elif isinstance(module, BloomBlock):
            attn_ln = module.input_layernorm
            qkv = module.self_attention.query_key_value
            qkv_input_scales = scales[name + '.self_attention.query_key_value']
            smooth_ln_fcs(attn_ln, qkv, qkv_input_scales, alpha)

            ffn_ln = module.post_attention_layernorm
            fc1 = module.mlp.dense_h_to_4h
            fc1_input_scales = scales[name + '.mlp.dense_h_to_4h']
            smooth_ln_fcs(ffn_ln, fc1, fc1_input_scales, alpha)


@torch.no_grad()
def smooth_layer(name, module, scales, alpha=0.5):
    if isinstance(module, OPTDecoderLayer):
        attn_ln = module.self_attn_layer_norm
        qkv = [module.self_attn.q_proj,
                module.self_attn.k_proj, module.self_attn.v_proj]
        qkv_input_scales = scales[name + '.self_attn.q_proj']
        smooth_ln_fcs(attn_ln, qkv, qkv_input_scales, alpha)

        ffn_ln = module.final_layer_norm
        fc1 = module.fc1
        fc1_input_scales = scales[name + '.fc1']
        smooth_ln_fcs(ffn_ln, fc1, fc1_input_scales, alpha)
    elif isinstance(module, LlamaDecoderLayer):
        attn_ln = module.input_layernorm
        qkv = [module.self_attn.q_proj,
                module.self_attn.k_proj, module.self_attn.v_proj]
        qkv_input_scales = scales[name + '.self_attn.q_proj']
        smooth_ln_fcs(attn_ln, qkv, qkv_input_scales, alpha)

        ffn_ln = module.post_attention_layernorm
        fc1 = [module.mlp.gate_proj,
                module.mlp.up_proj]
        fc1_input_scales = scales[name + '.mlp.gate_proj']
        smooth_ln_fcs(ffn_ln, fc1, fc1_input_scales, alpha)
    elif 'FalconDecoderLayer' in module.__class__.__name__:
        attn_ln = module.input_layernorm
        qkv_fc1 = [module.self_attention.query_key_value, module.mlp.dense_h_to_4h]
        qkv_input_scales = scales[name + '.self_attention.query_key_value']
        smooth_ln_fcs(attn_ln, qkv_fc1, qkv_input_scales, alpha)
    elif 'GLMBlock' in module.__class__.__name__:
        attn_ln = module.input_layernorm
        qkv = [module.self_attention.query_key_value]
        qkv_input_scales = scales[name + '.self_attention.query_key_value']
        smooth_ln_fcs(attn_ln, qkv, qkv_input_scales, alpha)
        ffn_ln = module.post_attention_layernorm
        fc1 = [module.mlp.dense_h_to_4h]
        fc1_input_scales = scales[name + '.mlp.dense_h_to_4h']
        smooth_ln_fcs(ffn_ln, fc1, fc1_input_scales, alpha)
    else:
        raise TypeError(f"{module} not supported!")