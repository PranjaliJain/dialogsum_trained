## Install these before running:

# !git clone https://github.com/PranjaliJain/dialogsum_trained.git
# !git pull 
# !pip install datasets
# !pip install transformers
# !pip install rouge_score

# Run using: 
# !python3 /content/dialogsum_trained/training/dialogsum_bart_tokenizer.py

import json
from datasets import load_metric,Dataset,DatasetDict
from transformers import AutoModelForSeq2SeqLM, DataCollatorForSeq2Seq, Seq2SeqTrainingArguments, Seq2SeqTrainer
from transformers import BartForConditionalGeneration, BartTokenizer
from transformers import AutoTokenizer
# import os
from torch import nn 
import torch

# os.environ['CUDA_VISIBLE_DEVICES']="6,7"

model_checkpoint = "facebook/bart-large"
metric = load_metric("rouge")

TEST_SUMMARY_ID = 1


def transform_single_dialogsumm_file(file):
    data = open(file,"r").readlines()
    result = {"fname":[],"summary":[],"dialogue":[]}
    for i in data:
        d = json.loads(i)
        for j in d.keys():
            if j in result.keys():
                result[j].append(d[j])
    return Dataset.from_dict(result)

def transform_test_file(file):
    data = open(file,"r").readlines()
    result = {"fname":[],"summary%d"%TEST_SUMMARY_ID:[],"dialogue":[]}
    for i in data:
        d = json.loads(i)
        for j in d.keys():
            if j in result.keys():
                result[j].append(d[j])
    
    result["summary"] = result["summary%d"%TEST_SUMMARY_ID]
    return Dataset.from_dict(result)

def transform_dialogsumm_to_huggingface_dataset(train,validation,test):
    train = transform_single_dialogsumm_file(train)
    validation = transform_single_dialogsumm_file(validation)
    test = transform_test_file(test)
    return DatasetDict({"train":train,"validation":validation,"test":test})

raw_datasets = transform_dialogsumm_to_huggingface_dataset("/content/dialogsum_trained/DialogSum_Data/dialogsum.train.jsonl","/content/dialogsum_trained/DialogSum_Data/dialogsum.dev.jsonl","/content/dialogsum_trained/DialogSum_Data/dialogsum.test.jsonl")

torch_device = 'cuda' if torch.cuda.is_available() else 'cpu'


model = BartForConditionalGeneration.from_pretrained(model_checkpoint)
tokenizer = BartTokenizer.from_pretrained(model_checkpoint)

def freeze_params(model: nn.Module):
    """Set requires_grad=False for each of model.parameters()"""
    for par in model.parameters():
        par.requires_grad = False

def freeze_embeds(model):
    """Freeze token embeddings and positional embeddings for BART and PEGASUS, just token embeddings for t5."""
    model_type = model.config.model_type
    if model_type == "t5":
        freeze_params(model.shared)
        for d in [model.encoder, model.decoder]:
            freeze_params(d.embed_tokens)
    else:
        freeze_params(model.model.shared)
        for d in [model.model.encoder, model.model.decoder]:
            freeze_params(d.embed_positions)
            freeze_params(d.embed_tokens)

freeze_embeds(model)

max_input_length = 256
max_target_length = 128

def preprocess_function(examples):
    inputs = [doc for doc in examples["dialogue"]]
    model_inputs = tokenizer(inputs, max_length=max_input_length, truncation=True)

    # Setup the tokenizer for targets
    with tokenizer.as_target_tokenizer():
        labels = tokenizer(examples["summary"], max_length=max_target_length, truncation=True)

    model_inputs["labels"] = labels["input_ids"]
    return model_inputs

tokenized_datasets = raw_datasets.map(preprocess_function, batched=True)


## use batch_size = 1 to get time around 8 hrs with GPU
## use batch_size = 16 to get time around 4 hrs with GPU - but it goes out of memory
batch_size = 1
args = Seq2SeqTrainingArguments(
    "BART-LARGE-DIALOGSUM",
    evaluation_strategy = "epoch",
    learning_rate=2e-5,
    per_device_train_batch_size=batch_size,
    per_device_eval_batch_size=batch_size,
    weight_decay=0.01,
    save_total_limit=3,
    num_train_epochs=3,
    predict_with_generate=True,
    fp16=True,
    # save_strategy="epoch",
    # metric_for_best_model="eval_rouge1",
    # greater_is_better=True,
    # seed=42,
    # generation_max_length=max_target_length,
    push_to_hub=False,
    output_dir = '/content/drive/MyDrive/project/results/dialogsumm-bart', 
    logging_dir = '/content/drive/MyDrive/project/results/dialogsumm-bart'
)



data_collator = DataCollatorForSeq2Seq(tokenizer, model=model)

import nltk
import numpy as np

def compute_metrics(eval_pred):
    predictions, labels = eval_pred
    decoded_preds = tokenizer.batch_decode(predictions, skip_special_tokens=True)
    # Replace -100 in the labels as we can't decode them.
    labels = np.where(labels != -100, labels, tokenizer.pad_token_id)
    decoded_labels = tokenizer.batch_decode(labels, skip_special_tokens=True)
    
    # Rouge expects a newline after each sentence
    decoded_preds = ["\n".join(nltk.sent_tokenize(pred.strip())) for pred in decoded_preds]
    decoded_labels = ["\n".join(nltk.sent_tokenize(label.strip())) for label in decoded_labels]
    
    result = metric.compute(predictions=decoded_preds, references=decoded_labels, use_stemmer=True)
    # Extract a few results
    result = {key: value.mid.fmeasure * 100 for key, value in result.items()}
    
    # Add mean generated length
    prediction_lens = [np.count_nonzero(pred != tokenizer.pad_token_id) for pred in predictions]
    result["gen_len"] = np.mean(prediction_lens)
    
    return {k: round(v, 4) for k, v in result.items()}



trainer = Seq2SeqTrainer(
    model,
    args,
    train_dataset=tokenized_datasets["train"],
    eval_dataset=tokenized_datasets["validation"],
    data_collator=data_collator,
    tokenizer=tokenizer,
    compute_metrics=compute_metrics
)


trainer.train()




out = trainer.predict(tokenized_datasets["test"],num_beams=5)

predictions, labels ,metric= out
print(metric)


decoded_preds = tokenizer.batch_decode(predictions, skip_special_tokens=True)
    # Replace -100 in the labels as we can't decode them.
labels = np.where(labels != -100, labels, tokenizer.pad_token_id)
decoded_labels = tokenizer.batch_decode(labels, skip_special_tokens=True)
    
    # Rouge expects a newline after e ach sentence
decoded_preds = [" ".join(nltk.sent_tokenize(pred.strip())) for pred in decoded_preds]
decoded_labels = [" ".join(nltk.sent_tokenize(label.strip())) for label in decoded_labels]


torch.save(model, '/content/drive/MyDrive/project/models/dialogsumm-bart')

# output summaries on test set
with open("test_output.txt","w") as f: 
    for i in decoded_preds:
        print(i)
        f.write(i.replace("\n","")+"\n")