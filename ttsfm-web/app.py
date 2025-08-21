"""
TTSFM Web Application

A Flask web application that provides a user-friendly interface
for the TTSFM text-to-speech package.
"""

import os
import json
import logging
import tempfile
import io
from datetime import datetime
from pathlib import Path
from typing import Dict, Any, Optional, List
from functools import wraps
from urllib.parse import urlparse, urljoin

from flask import Flask, request, jsonify, send_file, Response, render_template, redirect, url_for
from flask_cors import CORS
from flask_socketio import SocketIO
from dotenv import load_dotenv

# Import i18n support
from i18n import init_i18n, get_locale, set_locale, _

# Import the TTSFM package
try:
    from ttsfm import TTSClient, Voice, AudioFormat, TTSException
    from ttsfm.exceptions import APIException, NetworkException, ValidationException
    from ttsfm.utils import validate_text_length, split_text_by_length
except ImportError:
    # Fallback for development when package is not installed
    import sys
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
    from ttsfm import TTSClient, Voice, AudioFormat, TTSException
    from ttsfm.exceptions import APIException, NetworkException, ValidationException
    from ttsfm.utils import validate_text_length, split_text_by_length

# Load environment variables
load_dotenv()

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Create Flask app
app = Flask(__name__, static_folder='static', static_url_path='/static')
app.secret_key = os.getenv("SECRET_KEY", "ttsfm-secret-key-change-in-production")
CORS(app)

# Configuration (moved up for socketio initialization)
HOST = os.getenv("HOST", "localhost")
PORT = int(os.getenv("PORT", "8000"))
DEBUG = os.getenv("DEBUG", "false").lower() == "true"

# Initialize SocketIO with proper async mode
# Using eventlet for production, threading for development
async_mode = 'eventlet' if not DEBUG else 'threading'
socketio = SocketIO(app, cors_allowed_origins="*", async_mode=async_mode)

# Initialize i18n support
init_i18n(app)

# API Key configuration
API_KEY = os.getenv("TTSFM_API_KEY")  # Set this environment variable for API protection
REQUIRE_API_KEY = os.getenv("REQUIRE_API_KEY", "false").lower() == "true"

# Create TTS client - now uses openai.fm directly, no configuration needed
tts_client = TTSClient()

# Initialize WebSocket handler
from websocket_handler import WebSocketTTSHandler
websocket_handler = WebSocketTTSHandler(socketio, tts_client)

logger.info("Initialized web app with TTSFM using openai.fm free service")
logger.info(f"WebSocket support enabled with {async_mode} async mode")

# API Key validation decorator
def require_api_key(f):
    """Decorator to require API key for protected endpoints."""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        # Skip API key check if not required
        if not REQUIRE_API_KEY:
            return f(*args, **kwargs)

        # Check if API key is configured
        if not API_KEY:
            logger.warning("API key protection is enabled but TTSFM_API_KEY is not set")
            return jsonify({
                "error": "API key protection is enabled but not configured properly"
            }), 500

        # Get API key from request headers - prioritize Authorization header (OpenAI compatible)
        provided_key = None

        # 1. Check Authorization header first (OpenAI standard)
        auth_header = request.headers.get('Authorization')
        if auth_header and auth_header.startswith('Bearer '):
            provided_key = auth_header[7:]  # Remove 'Bearer ' prefix

        # 2. Check X-API-Key header as fallback
        if not provided_key:
            provided_key = request.headers.get('X-API-Key')

        # 3. Check API key from query parameters as fallback
        if not provided_key:
            provided_key = request.args.get('api_key')

        # 4. Check API key from JSON body as fallback
        if not provided_key and request.is_json:
            data = request.get_json(silent=True)
            if data:
                provided_key = data.get('api_key')

        # Validate API key
        if not provided_key or provided_key != API_KEY:
            logger.warning(f"Invalid API key attempt from {request.remote_addr}")
            return jsonify({
                "error": {
                    "message": "Invalid API key provided",
                    "type": "invalid_request_error",
                    "code": "invalid_api_key"
                }
            }), 401

        return f(*args, **kwargs)
    return decorated_function

def combine_audio_chunks(audio_chunks: List[bytes], format_type: str = "mp3") -> bytes:
    """
    Combine multiple audio chunks into a single audio file.

    Args:
        audio_chunks: List of audio data as bytes
        format_type: Audio format (mp3, wav, etc.)

    Returns:
        bytes: Combined audio data
    """
    try:
        # Try to use pydub for audio processing if available
        try:
            from pydub import AudioSegment

            # Convert each chunk to AudioSegment
            audio_segments = []
            for chunk in audio_chunks:
                if format_type.lower() == "mp3":
                    segment = AudioSegment.from_mp3(io.BytesIO(chunk))
                elif format_type.lower() == "wav":
                    segment = AudioSegment.from_wav(io.BytesIO(chunk))
                elif format_type.lower() == "opus":
                    # For OPUS, we'll treat it as WAV since openai.fm returns WAV for OPUS requests
                    segment = AudioSegment.from_wav(io.BytesIO(chunk))
                else:
                    # For other formats, try to auto-detect or default to WAV
                    try:
                        segment = AudioSegment.from_file(io.BytesIO(chunk))
                    except:
                        segment = AudioSegment.from_wav(io.BytesIO(chunk))

                audio_segments.append(segment)

            # Combine all segments
            combined = audio_segments[0]
            for segment in audio_segments[1:]:
                combined += segment

            # Export to bytes
            output_buffer = io.BytesIO()
            if format_type.lower() == "mp3":
                combined.export(output_buffer, format="mp3")
            elif format_type.lower() == "wav":
                combined.export(output_buffer, format="wav")
            else:
                # Default to the original format or WAV
                try:
                    combined.export(output_buffer, format=format_type.lower())
                except:
                    combined.export(output_buffer, format="wav")

            return output_buffer.getvalue()

        except ImportError:
            # Fallback: Simple concatenation for WAV files
            logger.warning("pydub not available, using simple concatenation for WAV files")

            if format_type.lower() == "wav":
                return _simple_wav_concatenation(audio_chunks)
            else:
                # For non-WAV formats without pydub, just concatenate raw bytes
                # This won't produce valid audio but is better than failing
                logger.warning(f"Cannot properly combine {format_type} files without pydub, using raw concatenation")
                return b''.join(audio_chunks)

    except Exception as e:
        logger.error(f"Error combining audio chunks: {e}")
        # Fallback to simple concatenation
        return b''.join(audio_chunks)

def _simple_wav_concatenation(wav_chunks: List[bytes]) -> bytes:
    """
    Simple WAV file concatenation without external dependencies.
    This is a basic implementation that works for simple WAV files.
    """
    if not wav_chunks:
        return b''

    if len(wav_chunks) == 1:
        return wav_chunks[0]

    try:
        # For WAV files, we can do a simple concatenation by:
        # 1. Taking the header from the first file
        # 2. Concatenating all the audio data
        # 3. Updating the file size in the header

        first_wav = wav_chunks[0]
        if len(first_wav) < 44:  # WAV header is at least 44 bytes
            return b''.join(wav_chunks)

        # Extract header from first file (first 44 bytes)
        header = bytearray(first_wav[:44])

        # Collect all audio data (skip headers for subsequent files)
        audio_data = first_wav[44:]  # Audio data from first file

        for wav_chunk in wav_chunks[1:]:
            if len(wav_chunk) > 44:
                audio_data += wav_chunk[44:]  # Skip header, append audio data

        # Update file size in header (bytes 4-7)
        total_size = len(header) + len(audio_data) - 8
        header[4:8] = total_size.to_bytes(4, byteorder='little')

        # Update data chunk size in header (bytes 40-43)
        data_size = len(audio_data)
        header[40:44] = data_size.to_bytes(4, byteorder='little')

        return bytes(header) + audio_data

    except Exception as e:
        logger.error(f"Error in simple WAV concatenation: {e}")
        # Ultimate fallback
        return b''.join(wav_chunks)

def _is_safe_url(target: Optional[str]) -> bool:
    """Validate that a target URL is safe for redirection.

    Allows only relative URLs or absolute URLs that match this server's host
    and http/https schemes. Prevents open redirects to external domains.
    Handles backslashes and malformed URLs.
    """
    if not target or target.strip() == "" or target.startswith("#"):
        return False

    # Remove backslashes (browsers treat them as slashes)
    target = target.replace('\\', '')

    # Parse the target URL
    ref_url = urlparse(request.host_url)
    test_url = urlparse(urljoin(request.host_url, target))

    # Only allow relative URLs (no scheme/netloc) or absolute URLs to this host
    parsed_target = urlparse(target)
    if not parsed_target.netloc and not parsed_target.scheme:
        # Disallow fragments and suspicious paths
        if parsed_target.path and not parsed_target.path.startswith("//"):
            return True
        return False
    return (
        test_url.scheme in ("http", "https")
        and ref_url.netloc == test_url.netloc
    )
@app.route('/set-language/<lang_code>')
def set_language(lang_code):
    """Set the user's language preference."""
    if set_locale(lang_code):
        # Redirect back only if the referrer is safe; otherwise go home
        target = request.referrer
        if target and _is_safe_url(target):
            return redirect(target)
        return redirect(url_for('index'))
    else:
        # Invalid language code, redirect to home
        return redirect(url_for('index'))

@app.route('/')
def index():
    """Serve the main web interface."""
    return render_template('index.html')

@app.route('/playground')
def playground():
    """Serve the interactive playground."""
    return render_template('playground.html')

@app.route('/docs')
def docs():
    """Serve the API documentation."""
    return render_template('docs.html')

@app.route('/websocket-demo')
def websocket_demo():
    """Serve the WebSocket streaming demo page."""
    return render_template('websocket_demo.html')

@app.route('/api/voices', methods=['GET'])
def get_voices():
    """Get list of available voices."""
    try:
        voices = [
            {
                "id": voice.value,
                "name": voice.value.title(),
                "description": f"{voice.value.title()} voice"
            }
            for voice in Voice
        ]
        
        return jsonify({
            "voices": voices,
            "count": len(voices)
        })
        
    except Exception as e:
        logger.error(f"Error getting voices: {e}")
        return jsonify({"error": "Failed to get voices"}), 500

@app.route('/api/formats', methods=['GET'])
def get_formats():
    """Get list of supported audio formats."""
    try:
        formats = [
            {
                "id": "mp3",
                "name": "MP3",
                "mime_type": "audio/mpeg",
                "description": "MP3 audio format - good quality, small file size",
                "quality": "Good",
                "file_size": "Small",
                "use_case": "Web, mobile apps, general use"
            },
            {
                "id": "opus",
                "name": "OPUS",
                "mime_type": "audio/opus",
                "description": "OPUS audio format - excellent quality, small file size",
                "quality": "Excellent",
                "file_size": "Small",
                "use_case": "Web streaming, VoIP"
            },
            {
                "id": "aac",
                "name": "AAC",
                "mime_type": "audio/aac",
                "description": "AAC audio format - good quality, medium file size",
                "quality": "Good",
                "file_size": "Medium",
                "use_case": "Apple devices, streaming"
            },
            {
                "id": "flac",
                "name": "FLAC",
                "mime_type": "audio/flac",
                "description": "FLAC audio format - lossless quality, large file size",
                "quality": "Lossless",
                "file_size": "Large",
                "use_case": "High-quality archival"
            },
            {
                "id": "wav",
                "name": "WAV",
                "mime_type": "audio/wav",
                "description": "WAV audio format - lossless quality, large file size",
                "quality": "Lossless",
                "file_size": "Large",
                "use_case": "Professional audio"
            },
            {
                "id": "pcm",
                "name": "PCM",
                "mime_type": "audio/pcm",
                "description": "PCM audio format - raw audio data, large file size",
                "quality": "Raw",
                "file_size": "Large",
                "use_case": "Audio processing"
            }
        ]

        return jsonify({
            "formats": formats,
            "count": len(formats)
        })

    except Exception as e:
        logger.error(f"Error getting formats: {e}")
        return jsonify({"error": "Failed to get formats"}), 500

@app.route('/api/validate-text', methods=['POST'])
def validate_text():
    """Validate text length and provide splitting suggestions."""
    try:
        data = request.get_json()
        if not data:
            return jsonify({"error": "No JSON data provided"}), 400

        text = data.get('text', '').strip()
        max_length = data.get('max_length', 4096)

        if not text:
            return jsonify({"error": "Text is required"}), 400

        text_length = len(text)
        is_valid = text_length <= max_length

        result = {
            "text_length": text_length,
            "max_length": max_length,
            "is_valid": is_valid,
            "needs_splitting": not is_valid
        }

        if not is_valid:
            # Provide splitting suggestions
            chunks = split_text_by_length(text, max_length, preserve_words=True)
            result.update({
                "suggested_chunks": len(chunks),
                "chunk_preview": [chunk[:100] + "..." if len(chunk) > 100 else chunk for chunk in chunks[:3]]
            })

        return jsonify(result)

    except Exception as e:
        logger.error(f"Text validation error: {e}")
        return jsonify({"error": "Text validation failed"}), 500

@app.route('/api/generate', methods=['POST'])
@require_api_key
def generate_speech():
    """Generate speech from text using the TTSFM package."""
    try:
        # Parse request data
        data = request.get_json()
        if not data:
            return jsonify({"error": "No JSON data provided"}), 400

        # Extract parameters
        text = data.get('text', '').strip()
        voice = data.get('voice', Voice.ALLOY.value)
        response_format = data.get('format', AudioFormat.MP3.value)
        instructions = data.get('instructions', '').strip() or None
        max_length = data.get('max_length', 4096)
        validate_length = data.get('validate_length', True)
        
        # Validate required fields
        if not text:
            return jsonify({"error": "Text is required"}), 400
        
        # Validate voice
        try:
            voice_enum = Voice(voice.lower())
        except ValueError:
            return jsonify({
                "error": f"Invalid voice: {voice}. Must be one of: {[v.value for v in Voice]}"
            }), 400
        
        # Validate format
        try:
            format_enum = AudioFormat(response_format.lower())
        except ValueError:
            return jsonify({
                "error": f"Invalid format: {response_format}. Must be one of: {[f.value for f in AudioFormat]}"
            }), 400
        
        logger.info(f"Generating speech: text='{text[:50]}...', voice={voice}, format={response_format}")
        
        # Generate speech using the TTSFM package with validation
        response = tts_client.generate_speech(
            text=text,
            voice=voice_enum,
            response_format=format_enum,
            instructions=instructions,
            max_length=max_length,
            validate_length=validate_length
        )
        
        # Return audio data
        return Response(
            response.audio_data,
            mimetype=response.content_type,
            headers={
                'Content-Disposition': f'attachment; filename="speech.{response.format.value}"',
                'Content-Length': str(response.size),
                'X-Audio-Format': response.format.value,
                'X-Audio-Size': str(response.size)
            }
        )
        
    except ValidationException as e:
        logger.warning(f"Validation error: {e}")
        return jsonify({"error": "Invalid input parameters"}), 400

    except APIException as e:
        logger.error(f"API error: {e}")
        return jsonify({
            "error": "TTS service error",
            "status_code": getattr(e, 'status_code', 500)
        }), getattr(e, 'status_code', 500)

    except NetworkException as e:
        logger.error(f"Network error: {e}")
        return jsonify({
            "error": "TTS service is currently unavailable"
        }), 503

    except TTSException as e:
        logger.error(f"TTS error: {e}")
        return jsonify({"error": "Text-to-speech generation failed"}), 500
        
    except Exception as e:
        logger.error(f"Unexpected error: {e}")
        return jsonify({"error": "Internal server error"}), 500



@app.route('/api/generate-combined', methods=['POST'])
@require_api_key
def generate_speech_combined():
    """Generate speech from long text and return a single combined audio file."""
    try:
        data = request.get_json()
        if not data:
            return jsonify({"error": "No JSON data provided"}), 400

        text = data.get('text', '').strip()
        voice = data.get('voice', Voice.ALLOY.value)
        response_format = data.get('format', AudioFormat.MP3.value)
        instructions = data.get('instructions', '').strip() or None
        max_length = data.get('max_length', 4096)
        preserve_words = data.get('preserve_words', True)

        if not text:
            return jsonify({"error": "Text is required"}), 400

        # Check if text needs splitting
        if len(text) <= max_length:
            # Text is short enough, use regular generation
            try:
                voice_enum = Voice(voice.lower())
                format_enum = AudioFormat(response_format.lower())
            except ValueError as e:
                logger.warning(f"Invalid voice or format: {e}")
                return jsonify({"error": "Invalid voice or format specified"}), 400

            response = tts_client.generate_speech(
                text=text,
                voice=voice_enum,
                response_format=format_enum,
                instructions=instructions,
                max_length=max_length,
                validate_length=True
            )

            return Response(
                response.audio_data,
                mimetype=response.content_type,
                headers={
                    'Content-Disposition': f'attachment; filename="combined_speech.{response.format.value}"',
                    'Content-Length': str(response.size),
                    'X-Audio-Format': response.format.value,
                    'X-Audio-Size': str(response.size),
                    'X-Chunks-Combined': '1'
                }
            )

        # Text is long, split and combine
        try:
            voice_enum = Voice(voice.lower())
            format_enum = AudioFormat(response_format.lower())
        except ValueError as e:
            logger.warning(f"Invalid voice or format: {e}")
            return jsonify({"error": "Invalid voice or format specified"}), 400

        logger.info(f"Generating combined speech for long text: {len(text)} characters, splitting into chunks")

        # Generate speech chunks
        try:
            responses = tts_client.generate_speech_long_text(
                text=text,
                voice=voice_enum,
                response_format=format_enum,
                instructions=instructions,
                max_length=max_length,
                preserve_words=preserve_words
            )
        except Exception as e:
            logger.error(f"Long text generation failed: {e}")
            return jsonify({"error": "Long text generation failed"}), 500

        if not responses:
            return jsonify({"error": "No valid text chunks found"}), 400

        logger.info(f"Generated {len(responses)} chunks, combining into single audio file")

        # Extract audio data from responses
        audio_chunks = [response.audio_data for response in responses]

        # Combine audio chunks
        try:
            combined_audio = combine_audio_chunks(audio_chunks, format_enum.value)
        except Exception as e:
            logger.error(f"Failed to combine audio chunks: {e}")
            return jsonify({"error": "Failed to combine audio chunks"}), 500

        if not combined_audio:
            return jsonify({"error": "Failed to generate combined audio"}), 500

        # Determine content type
        content_type = responses[0].content_type  # Use content type from first chunk

        logger.info(f"Successfully combined {len(responses)} chunks into single audio file ({len(combined_audio)} bytes)")

        return Response(
            combined_audio,
            mimetype=content_type,
            headers={
                'Content-Disposition': f'attachment; filename="combined_speech.{format_enum.value}"',
                'Content-Length': str(len(combined_audio)),
                'X-Audio-Format': format_enum.value,
                'X-Audio-Size': str(len(combined_audio)),
                'X-Chunks-Combined': str(len(responses)),
                'X-Original-Text-Length': str(len(text))
            }
        )

    except ValidationException as e:
        logger.warning(f"Validation error: {e}")
        return jsonify({"error": "Invalid input parameters"}), 400

    except APIException as e:
        logger.error(f"API error: {e}")
        return jsonify({
            "error": "TTS service error",
            "status_code": getattr(e, 'status_code', 500)
        }), getattr(e, 'status_code', 500)

    except NetworkException as e:
        logger.error(f"Network error: {e}")
        return jsonify({
            "error": "TTS service is currently unavailable"
        }), 503

    except TTSException as e:
        logger.error(f"TTS error: {e}")
        return jsonify({"error": "Text-to-speech generation failed"}), 500

    except Exception as e:
        logger.error(f"Combined generation error: {e}")
        return jsonify({"error": "Combined audio generation failed"}), 500

@app.route('/api/status', methods=['GET'])
def get_status():
    """Get service status."""
    try:
        # Try to make a simple request to check if the TTS service is available
        test_response = tts_client.generate_speech(
            text="test",
            voice=Voice.ALLOY,
            response_format=AudioFormat.MP3
        )
        
        return jsonify({
            "status": "online",
            "tts_service": "openai.fm (free)",
            "package_version": "3.2.3",
            "timestamp": datetime.now().isoformat()
        })
        
    except Exception as e:
        logger.error(f"Status check failed: {e}")
        return jsonify({
            "status": "error",
            "tts_service": "openai.fm (free)",
            "error": "Service status check failed",
            "timestamp": datetime.now().isoformat()
        }), 503

@app.route('/api/health', methods=['GET'])
def health_check():
    """Simple health check endpoint."""
    return jsonify({
        "status": "healthy",
        "package_version": "3.2.3",
        "timestamp": datetime.now().isoformat()
    })

@app.route('/api/websocket/status', methods=['GET'])
def websocket_status():
    """Get WebSocket server status and active connections."""
    return jsonify({
        "websocket_enabled": True,
        "async_mode": async_mode,
        "active_sessions": websocket_handler.get_active_sessions_count(),
        "transport_options": ["websocket", "polling"],
        "endpoint": f"ws{'s' if request.is_secure else ''}://{request.host}/socket.io/",
        "timestamp": datetime.now().isoformat()
    })

@app.route('/api/auth-status', methods=['GET'])
def auth_status():
    """Get authentication status and requirements."""
    return jsonify({
        "api_key_required": REQUIRE_API_KEY,
        "api_key_configured": bool(API_KEY) if REQUIRE_API_KEY else None,
        "timestamp": datetime.now().isoformat()
    })

@app.route('/api/translations/<lang_code>', methods=['GET'])
def get_translations(lang_code):
    """Get translations for a specific language."""
    try:
        if hasattr(app, 'language_manager'):
            translations = app.language_manager.translations.get(lang_code, {})
            return jsonify(translations)
        else:
            return jsonify({}), 404
    except Exception as e:
        logger.error(f"Error getting translations for {lang_code}: {e}")
        return jsonify({"error": "Failed to get translations"}), 500

# OpenAI-compatible API endpoints
@app.route('/v1/audio/speech', methods=['POST'])
@require_api_key
def openai_speech():
    """OpenAI-compatible speech generation endpoint with auto-combine feature."""
    try:
        # Parse request data
        data = request.get_json()
        if not data:
            return jsonify({
                "error": {
                    "message": "No JSON data provided",
                    "type": "invalid_request_error",
                    "code": "missing_data"
                }
            }), 400

        # Extract OpenAI-compatible parameters
        model = data.get('model', 'gpt-4o-mini-tts')  # Accept but ignore model
        input_text = data.get('input', '').strip()
        voice = data.get('voice', 'alloy')
        response_format = data.get('response_format', 'mp3')
        instructions = data.get('instructions', '').strip() or None
        speed = data.get('speed', 1.0)  # Accept but ignore speed

        # TTSFM-specific parameters
        auto_combine = data.get('auto_combine', True)  # New parameter: auto-combine long text (default: True)
        max_length = data.get('max_length', 4096)  # Custom parameter for chunk size

        # Validate required fields
        if not input_text:
            return jsonify({
                "error": {
                    "message": "Input text is required",
                    "type": "invalid_request_error",
                    "code": "missing_input"
                }
            }), 400

        # Validate voice
        try:
            voice_enum = Voice(voice.lower())
        except ValueError:
            return jsonify({
                "error": {
                    "message": f"Invalid voice: {voice}. Must be one of: {[v.value for v in Voice]}",
                    "type": "invalid_request_error",
                    "code": "invalid_voice"
                }
            }), 400

        # Validate format
        try:
            format_enum = AudioFormat(response_format.lower())
        except ValueError:
            return jsonify({
                "error": {
                    "message": f"Invalid response_format: {response_format}. Must be one of: {[f.value for f in AudioFormat]}",
                    "type": "invalid_request_error",
                    "code": "invalid_format"
                }
            }), 400

        logger.info(f"OpenAI API: Generating speech: text='{input_text[:50]}...', voice={voice}, format={response_format}, auto_combine={auto_combine}")

        # Check if text exceeds limit and auto_combine is enabled
        if len(input_text) > max_length and auto_combine:
            # Long text with auto-combine enabled: split and combine
            logger.info(f"Long text detected ({len(input_text)} chars), auto-combining enabled")

            # Generate speech chunks
            responses = tts_client.generate_speech_long_text(
                text=input_text,
                voice=voice_enum,
                response_format=format_enum,
                instructions=instructions,
                max_length=max_length,
                preserve_words=True
            )

            if not responses:
                return jsonify({
                    "error": {
                        "message": "No valid text chunks found",
                        "type": "processing_error",
                        "code": "no_chunks"
                    }
                }), 400

            # Extract audio data and combine
            audio_chunks = [response.audio_data for response in responses]
            combined_audio = combine_audio_chunks(audio_chunks, format_enum.value)

            if not combined_audio:
                return jsonify({
                    "error": {
                        "message": "Failed to combine audio chunks",
                        "type": "processing_error",
                        "code": "combine_failed"
                    }
                }), 500

            content_type = responses[0].content_type

            logger.info(f"Successfully combined {len(responses)} chunks into single audio file")

            return Response(
                combined_audio,
                mimetype=content_type,
                headers={
                    'Content-Type': content_type,
                    'Content-Length': str(len(combined_audio)),
                    'X-Audio-Format': format_enum.value,
                    'X-Audio-Size': str(len(combined_audio)),
                    'X-Chunks-Combined': str(len(responses)),
                    'X-Original-Text-Length': str(len(input_text)),
                    'X-Auto-Combine': 'true',
                    'X-Powered-By': 'TTSFM-OpenAI-Compatible'
                }
            )

        else:
            # Short text or auto_combine disabled: use regular generation
            if len(input_text) > max_length and not auto_combine:
                # Text is too long but auto_combine is disabled - return error
                return jsonify({
                    "error": {
                        "message": f"Input text is too long ({len(input_text)} characters). Maximum allowed length is {max_length} characters. Enable auto_combine parameter to automatically split and combine long text.",
                        "type": "invalid_request_error",
                        "code": "text_too_long"
                    }
                }), 400

            # Generate speech using the TTSFM package
            response = tts_client.generate_speech(
                text=input_text,
                voice=voice_enum,
                response_format=format_enum,
                instructions=instructions,
                max_length=max_length,
                validate_length=True
            )

            # Return audio data in OpenAI format
            return Response(
                response.audio_data,
                mimetype=response.content_type,
                headers={
                    'Content-Type': response.content_type,
                    'Content-Length': str(response.size),
                    'X-Audio-Format': response.format.value,
                    'X-Audio-Size': str(response.size),
                    'X-Chunks-Combined': '1',
                    'X-Auto-Combine': str(auto_combine).lower(),
                    'X-Powered-By': 'TTSFM-OpenAI-Compatible'
                }
            )

    except ValidationException as e:
        logger.warning(f"OpenAI API validation error: {e}")
        return jsonify({
            "error": {
                "message": "Invalid request parameters",
                "type": "invalid_request_error",
                "code": "validation_error"
            }
        }), 400

    except APIException as e:
        logger.error(f"OpenAI API error: {e}")
        return jsonify({
            "error": {
                "message": "Text-to-speech generation failed",
                "type": "api_error",
                "code": "tts_error"
            }
        }), getattr(e, 'status_code', 500)

    except NetworkException as e:
        logger.error(f"OpenAI API network error: {e}")
        return jsonify({
            "error": {
                "message": "TTS service is currently unavailable",
                "type": "service_unavailable_error",
                "code": "service_unavailable"
            }
        }), 503

    except Exception as e:
        logger.error(f"OpenAI API unexpected error: {e}")
        return jsonify({
            "error": {
                "message": "An unexpected error occurred",
                "type": "internal_error",
                "code": "internal_error"
            }
        }), 500



@app.route('/v1/models', methods=['GET'])
def openai_models():
    """OpenAI-compatible models endpoint."""
    return jsonify({
        "object": "list",
        "data": [
            {
                "id": "gpt-4o-mini-tts",
                "object": "model",
                "created": 1699564800,
                "owned_by": "ttsfm",
                "permission": [],
                "root": "gpt-4o-mini-tts",
                "parent": None
            }
        ]
    })

@app.errorhandler(404)
def not_found(error):
    """Handle 404 errors."""
    return jsonify({"error": "Endpoint not found"}), 404

@app.errorhandler(405)
def method_not_allowed(error):
    """Handle 405 errors."""
    return jsonify({"error": "Method not allowed"}), 405

@app.errorhandler(500)
def internal_error(error):
    """Handle 500 errors."""
    logger.error(f"Internal server error: {error}")
    return jsonify({"error": "Internal server error"}), 500

if __name__ == '__main__':
    logger.info(f"Starting TTSFM web application on {HOST}:{PORT}")
    logger.info("Using openai.fm free TTS service")
    logger.info(f"Debug mode: {DEBUG}")

    # Log API key protection status
    if REQUIRE_API_KEY:
        if API_KEY:
            logger.info("🔒 API key protection is ENABLED")
            logger.info("All TTS generation requests require a valid API key")
        else:
            logger.warning("⚠️  API key protection is enabled but TTSFM_API_KEY is not set!")
            logger.warning("Please set the TTSFM_API_KEY environment variable")
    else:
        logger.info("🔓 API key protection is DISABLED - all requests are allowed")
        logger.info("Set REQUIRE_API_KEY=true to enable API key protection")

    try:
        logger.info(f"Starting with {async_mode} async mode")
        socketio.run(app, host=HOST, port=PORT, debug=DEBUG)
    except KeyboardInterrupt:
        logger.info("Application stopped by user")
    except Exception as e:
        logger.error(f"Failed to start application: {e}")
    finally:
        # Clean up TTS client
        tts_client.close()
