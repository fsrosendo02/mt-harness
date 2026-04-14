FROM --platform=linux/amd64 nvcr.io/nvidia/cuda:13.0.1-cudnn-runtime-ubuntu24.04

ENV LC_ALL en_US.UTF-8
ENV TZ=America/Los_Angeles

RUN apt-get update
RUN apt-get install -y curl wget python3 openjdk-11-jdk nano zstd unzip gcc cpanminus git subversion universal-ctags screen python3-pip tzdata
RUN apt-get clean
RUN pip3 install --break-system-packages ollama
WORKDIR /root
RUN wget https://github.com/rjust/defects4j/archive/refs/tags/v3.0.1.tar.gz -O defects4j.tar.gz
RUN tar xf defects4j.tar.gz
RUN cd /root/defects4j-3.0.1;cpanm --installdeps --force .;./init.sh
RUN groupadd -g 30127 nfsusers --users root
ENV PATH "$PATH:/root/defects4j-3.0.1/framework/bin"
RUN curl -fsSL https://ollama.com/install.sh | sh
COPY mt-harness /root/mt-harness

# python3 build_defects4j_catalog.py --output test.json --projects Lang --bug-ids 1,3,4,5,6,7,8,9,10,11,12,13,14,15,16,17,19,20,21,22,23,24,26,27,28,29,30,31,32,33,34,35,36,37,38,39,40,41,42,43,44,45,46,47,49,50,51,52,53,54,55,56,57,58,59,60,61,62,63,64,65 --max-per-project 1000 --max-per-function 1000 --max-per-file 1000
#RUN rm -rf /var/lib/apt/lists/*


#ENV APP_DIR /home/
#WORKDIR ${APP_DIR}

#RUN ollama serve &
#RUN sleep 60;ollama pull mistral:7b
#RUN ollama pull phi4-mini:3.8b
#RUN ollama pull qwen3:14b
#RUN ollama pull deepseek-r1:8b
#RUN ollama pull qwen2.5:7b
#RUN ollama pull qwen3.5:9b
#RUN ollama pull deepseek-v2:16b
#RUN ollama pull deepseek-coder-v2:16b
#RUN ollama pull llama3.1:8b
#RUN ollama pull codellama:13b
#RUN ollama pull qwen2.5-coder:7b

# Now the ones that we can afford to do with a GPU
#RUN ollama pull qwen2.5-coder:32b 
# ou 14b qwen2.5 coder, depende do gpu
#RUN ollama pull qwen2.5-coder:7b
#RUN ollama pull codellama:34b
#RUN ollama pull phind-codellama:34b
#RUN ollama pull qwen2.5:14b
#RUN ollama pull deepseek-r1:14b


CMD ["ollama", "serve"] 


