from fastapi import FastAPI, HTTPException, UploadFile, File, BackgroundTasks
from fastapi.responses import JSONResponse
from pathlib import Path
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import shutil
import os
import asyncio
from typing import List, Optional
import uvicorn
from datetime import datetime
from uuid import uuid4
import aiofiles
import logging
from model.model import MalwareAnalyzer  # Import your existing analyzer

app = FastAPI(
    title="Malware Analysis API",
    description="API for analyzing potential malware in C files",
    version="1.0.0"
)

# Add CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # In production, replace with specific origins
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Create necessary directories
os.makedirs("uploads", exist_ok=True)
os.makedirs("reports", exist_ok=True)

class AnalysisRequest(BaseModel):
    job_id: str

class AnalysisResponse(BaseModel):
    job_id: str
    status: str
    result: Optional[str] = None
    error: Optional[str] = ""  # Changed to empty string default instead of None

# Store analysis jobs
analysis_jobs = {}

async def process_analysis(job_id: str, c_files: List[str], strings_file: str):
    try:
        analyzer = MalwareAnalyzer()
        result = await analyzer.analyze_malware_files(c_files, strings_file)
        
        # Update job status
        analysis_jobs[job_id] = {
            "status": "completed",
            "result": result,
            "error": ""  # Empty string instead of None
        }
        
        # Cleanup uploaded files
        for file in c_files:
            if os.path.exists(file):
                os.remove(file)
        if os.path.exists(strings_file):
            os.remove(strings_file)            
    except Exception as e:
        analysis_jobs[job_id] = {
            "status": "failed",
            "result": None,
            "error": str(e)
        }
        logging.error(f"Error processing job {job_id}: {str(e)}")


import subprocess


@app.post("/decompile/")
async def decompile_and_analyze(
    background_tasks: BackgroundTasks,
    binary_file: UploadFile = File(...)
):
    """
    Upload a binary file, decompile it, and analyze the generated C code.
    """
    binary_file = "hello.exe"
    try:
        # Generate unique job ID
        job_id = str(uuid4())
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

        # Create job directory
        job_dir = f"uploads/{job_id}"
        os.makedirs(job_dir, exist_ok=True)

        # Save uploaded binary file
        binary_path = f"{job_dir}/{binary_file}.exe"
        async with aiofiles.open(binary_path, 'wb') as out_file:
            content = await binary_file.read()
            await out_file.write(content)


        # Call the decompiler
        decompile_output_dir = f"output/{binary_file}"
        os.makedirs(decompile_output_dir, exist_ok=True)  # Ensure output dir exists

        decompiled_file = f"{decompile_output_dir}/{binary_file}.c"

        # Execute the decompiler

        try:
            subprocess.run(
                ["python3", "reversing.py", binary_path],
                check=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE
            )
        except subprocess.CalledProcessError as e:
            logging.error(f"Decompiler error: {e.stderr.decode()}")
            raise HTTPException(
                status_code=500, detail=f"Decompiler failed: {e.stderr.decode()}"
            )
        print(binary_file) 

        # Verify decompiled file exists
        if not os.path.exists(decompiled_file):
            raise HTTPException(
                status_code=500, detail="Decompiled file not found after processing"
            )

        # Add task to process the decompiled file with the upload logic
        async with aiofiles.open(decompiled_file, 'r') as c_file_content:
            c_code = await c_file_content.read()

        c_temp_path = f"{job_dir}/temp_decompiled.c"
        async with aiofiles.open(c_temp_path, 'w') as temp_c_file:
            await temp_c_file.write(c_code)

        # Prepare fake "UploadFile" for analysis
        class FakeUploadFile:
            def __init__(self, filename, file_content):
                self.filename = filename
                self.file_content = file_content

            async def read(self):
                return self.file_content

        # Create a fake UploadFile instance and call the existing `upload_files` logic
        fake_c_files = [FakeUploadFile(filename="decompiled.c", file_content=c_code)]
        fake_strings_file = FakeUploadFile(
            filename="placeholder.strings", file_content="Placeholder for strings"
        )

        return await upload_files(background_tasks, fake_c_files, fake_strings_file)

    except Exception as e:
        logging.error(f"Error in decompile_and_analyze: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Failed to process binary: {str(e)}")






@app.post("/upload/", response_model=AnalysisRequest)
async def upload_files(
    background_tasks: BackgroundTasks,
    c_files: List[UploadFile] = File(...),
    strings_file: UploadFile = File(...)
):
    """
    Upload C files and strings file for analysis.
    Returns a job ID for tracking the analysis progress.
    """
    try:
        job_id = str(uuid4())
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        
        # Create job directory
        job_dir = f"uploads/{job_id}"
        os.makedirs(job_dir, exist_ok=True)
        
        # Save uploaded files
        c_file_paths = []
        for c_file in c_files:
            file_path = f"{job_dir}/{c_file}.exe"
            async with aiofiles.open(file_path, 'wb') as out_file:
                content = await c_file.read()
                await out_file.write(content)
            c_file_paths.append(file_path)
        
        strings_path = f"{job_dir}/{strings_file}.exe"
        async with aiofiles.open(strings_path, 'wb') as out_file:
            content = await strings_file.read()
            await out_file.write(content)
        
        # Initialize job status
        analysis_jobs[job_id] = {
            "status": "processing",
            "result": None,
            "error": ""  # Empty string instead of None
        }
        
        # Start analysis in background
        background_tasks.add_task(
            process_analysis,
            job_id,
            c_file_paths,
            strings_path
        )
        
        return {"job_id": job_id}
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/status/{job_id}", response_model=AnalysisResponse)
async def get_analysis_status(job_id: str):
    """
    Get the status of an analysis job using its job ID.
    """
    if job_id not in analysis_jobs:
        raise HTTPException(status_code=404, detail="Job not found")
    
    job = analysis_jobs[job_id]
    return {
        "job_id": job_id,
        "status": job["status"],
        "result": job["result"],
        "error": job["error"]
    }

@app.delete("/job/{job_id}")
async def delete_job(job_id: str):
    """
    Delete a completed job and its associated files.
    """
    if job_id not in analysis_jobs:
        raise HTTPException(status_code=404, detail="Job not found")
    
    # Remove job directory if it exists
    job_dir = f"uploads/{job_id}"
    if os.path.exists(job_dir):
        shutil.rmtree(job_dir)
    
    # Remove job from memory
    del analysis_jobs[job_id]
    
    return {"message": f"Job {job_id} deleted successfully"}

if __name__ == "__main__":
    uvicorn.run("app:app", host="0.0.0.0", port=8000, reload=True)  # Changed host to 0.0.0.0
