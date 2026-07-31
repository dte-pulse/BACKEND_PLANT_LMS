from fastapi import APIRouter, Depends, HTTPException, status

from app.api.deps import get_topic_service
from app.schemas.topic import TopicCreate, TopicRead, TopicUpdate
from app.services.topic_service import TopicService

router = APIRouter(prefix='/topics', tags=['topics'])


@router.get('', response_model=list[TopicRead])
def list_topics(service: TopicService = Depends(get_topic_service)):
    return service.list_topics()


@router.post('', response_model=TopicRead)
def create_topic(payload: TopicCreate, service: TopicService = Depends(get_topic_service)):
    return service.create_topic(payload)


@router.get('/{topic_id}', response_model=TopicRead)
def get_topic(topic_id: int, service: TopicService = Depends(get_topic_service)):
    topic = service.get_topic(topic_id)
    if not topic:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail='Topic not found')
    return topic


@router.put('/{topic_id}', response_model=TopicRead)
def update_topic(topic_id: int, payload: TopicUpdate, service: TopicService = Depends(get_topic_service)):
    topic = service.update_topic(topic_id, payload)
    if not topic:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail='Topic not found')
    return topic


@router.delete('/{topic_id}', status_code=status.HTTP_204_NO_CONTENT)
def delete_topic(topic_id: int, service: TopicService = Depends(get_topic_service)):
    success = service.delete_topic(topic_id)
    if not success:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail='Topic not found')
    return None
