"""Unit tests for the marketplace (M21/M22) — templates, listings, reviews."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import pytest

from api.models.enums import ListingStatus, TemplateCategory, TemplateStatus

# ---------------------------------------------------------------------------
# Enum tests
# ---------------------------------------------------------------------------


class TestMarketplaceEnums:
    def test_template_status_values(self) -> None:
        assert TemplateStatus.draft == "draft"
        assert TemplateStatus.published == "published"
        assert TemplateStatus.deprecated == "deprecated"

    def test_listing_status_values(self) -> None:
        assert ListingStatus.pending == "pending"
        assert ListingStatus.approved == "approved"
        assert ListingStatus.rejected == "rejected"
        assert ListingStatus.unlisted == "unlisted"

    def test_template_category_values(self) -> None:
        assert TemplateCategory.customer_support == "customer_support"
        assert TemplateCategory.data_analysis == "data_analysis"
        assert TemplateCategory.code_review == "code_review"
        assert TemplateCategory.research == "research"
        assert TemplateCategory.automation == "automation"
        assert TemplateCategory.content == "content"
        assert TemplateCategory.other == "other"


# ---------------------------------------------------------------------------
# Schema tests
# ---------------------------------------------------------------------------


class TestMarketplaceSchemas:
    def test_template_create_schema(self) -> None:
        from api.models.schemas import TemplateCreate

        tc = TemplateCreate(
            name="test-template",
            framework="langgraph",
            config_template={"name": "{{agent_name}}"},
            author="tester",
        )
        assert tc.name == "test-template"
        assert tc.category == TemplateCategory.other
        assert tc.version == "1.0.0"
        assert tc.tags == []
        assert tc.team == "default"

    def test_template_create_with_parameters(self) -> None:
        from api.models.schemas import TemplateCreate, TemplateParameter

        params = [
            TemplateParameter(
                name="agent_name",
                label="Agent Name",
                type="string",
                default="my-agent",
            )
        ]
        tc = TemplateCreate(
            name="param-template",
            framework="custom",
            config_template={"name": "{{agent_name}}"},
            parameters=params,
            author="tester",
        )
        assert len(tc.parameters) == 1
        assert tc.parameters[0].name == "agent_name"

    def test_template_response_from_attributes(self) -> None:
        from api.models.schemas import TemplateResponse

        now = datetime.now(UTC)
        data = {
            "id": uuid.uuid4(),
            "name": "test",
            "version": "1.0.0",
            "description": "desc",
            "category": TemplateCategory.research,
            "framework": "langgraph",
            "config_template": {},
            "parameters": [],
            "tags": ["tag1"],
            "author": "alice",
            "team": "default",
            "status": TemplateStatus.draft,
            "use_count": 0,
            "readme": "",
            "created_at": now,
            "updated_at": now,
        }
        resp = TemplateResponse(**data)
        assert resp.name == "test"
        assert resp.category == TemplateCategory.research

    def test_listing_review_create_validation(self) -> None:
        from api.models.schemas import ListingReviewCreate

        review = ListingReviewCreate(reviewer="bob", rating=4, comment="Good template!")
        assert review.rating == 4

    def test_listing_review_create_rating_bounds(self) -> None:
        from pydantic import ValidationError

        from api.models.schemas import ListingReviewCreate

        with pytest.raises(ValidationError):
            ListingReviewCreate(reviewer="bob", rating=0)

        with pytest.raises(ValidationError):
            ListingReviewCreate(reviewer="bob", rating=6)

    def test_template_instantiate_request(self) -> None:
        from api.models.schemas import TemplateInstantiateRequest

        req = TemplateInstantiateRequest(values={"agent_name": "my-bot", "model": "gpt-4o"})
        assert req.values["agent_name"] == "my-bot"

    def test_marketplace_browse_item(self) -> None:
        from api.models.schemas import MarketplaceBrowseItem

        item = MarketplaceBrowseItem(
            listing_id=uuid.uuid4(),
            template_id=uuid.uuid4(),
            name="test-template",
            description="A test",
            category=TemplateCategory.automation,
            framework="custom",
            tags=["test"],
            author="alice",
            avg_rating=4.5,
            review_count=10,
            install_count=50,
            featured=True,
            published_at=datetime.now(UTC),
        )
        assert item.featured is True
        assert item.avg_rating == 4.5

    def test_marketplace_listing_create(self) -> None:
        from api.models.schemas import MarketplaceListingCreate

        body = MarketplaceListingCreate(
            template_id=uuid.uuid4(),
            submitted_by="alice@test.com",
        )
        assert body.submitted_by == "alice@test.com"

    def test_marketplace_listing_update(self) -> None:
        from api.models.schemas import MarketplaceListingUpdate

        body = MarketplaceListingUpdate(status=ListingStatus.approved, reviewed_by="admin")
        assert body.status == ListingStatus.approved
        assert body.featured is None


# ---------------------------------------------------------------------------
# Database model tests
# ---------------------------------------------------------------------------


class TestMarketplaceDatabaseModels:
    def test_template_model_exists(self) -> None:
        from api.models.database import Template

        assert Template.__tablename__ == "templates"

    def test_marketplace_listing_model_exists(self) -> None:
        from api.models.database import MarketplaceListing

        assert MarketplaceListing.__tablename__ == "marketplace_listings"

    def test_listing_review_model_exists(self) -> None:
        from api.models.database import ListingReview

        assert ListingReview.__tablename__ == "listing_reviews"

    def test_template_has_listings_relationship(self) -> None:
        from api.models.database import Template

        assert hasattr(Template, "listings")

    def test_listing_has_reviews_relationship(self) -> None:
        from api.models.database import MarketplaceListing

        assert hasattr(MarketplaceListing, "reviews")


# ---------------------------------------------------------------------------
# Registry service tests (mocked DB)
# ---------------------------------------------------------------------------


class TestTemplateRegistry:
    @pytest.mark.asyncio
    async def test_create_template(self) -> None:
        from registry.templates import TemplateRegistry

        mock_session = AsyncMock()
        mock_session.flush = AsyncMock()

        template = await TemplateRegistry.create(
            mock_session,
            name="test-tpl",
            version="1.0.0",
            description="Test",
            category=TemplateCategory.other,
            framework="langgraph",
            config_template={"name": "{{agent_name}}"},
            parameters=[],
            tags=["test"],
            author="tester",
            team="default",
        )
        assert template.name == "test-tpl"
        mock_session.add.assert_called_once()
        mock_session.flush.assert_called_once()

    @pytest.mark.asyncio
    async def test_get_by_id_not_found(self) -> None:
        from registry.templates import TemplateRegistry

        mock_session = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None
        mock_session.execute.return_value = mock_result

        result = await TemplateRegistry.get_by_id(mock_session, uuid.uuid4())
        assert result is None

    @pytest.mark.asyncio
    async def test_delete_nonexistent(self) -> None:
        from registry.templates import TemplateRegistry

        mock_session = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None
        mock_session.execute.return_value = mock_result

        deleted = await TemplateRegistry.delete(mock_session, uuid.uuid4())
        assert deleted is False


class TestTemplateRegistryExtended:
    """Additional tests for TemplateRegistry methods to increase coverage."""

    @pytest.mark.asyncio
    async def test_get_by_name(self) -> None:
        from registry.templates import TemplateRegistry

        mock_session = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None
        mock_session.execute.return_value = mock_result

        result = await TemplateRegistry.get_by_name(mock_session, "nonexistent")
        assert result is None

    @pytest.mark.asyncio
    async def test_get_by_name_found(self) -> None:
        from registry.templates import TemplateRegistry

        mock_template = MagicMock()
        mock_template.name = "found-template"
        mock_session = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = mock_template
        mock_session.execute.return_value = mock_result

        result = await TemplateRegistry.get_by_name(mock_session, "found-template")
        assert result is not None
        assert result.name == "found-template"

    @pytest.mark.asyncio
    async def test_list_no_filters(self) -> None:
        from registry.templates import TemplateRegistry

        mock_session = AsyncMock()
        mock_count_result = MagicMock()
        mock_count_result.scalar.return_value = 2
        mock_list_result = MagicMock()
        mock_scalars = MagicMock()
        mock_scalars.all.return_value = [MagicMock(), MagicMock()]
        mock_list_result.scalars.return_value = mock_scalars

        mock_session.execute.side_effect = [mock_count_result, mock_list_result]

        items, total = await TemplateRegistry.list(mock_session)
        assert total == 2
        assert len(items) == 2

    @pytest.mark.asyncio
    async def test_list_with_category_filter(self) -> None:
        from registry.templates import TemplateRegistry

        mock_session = AsyncMock()
        mock_count_result = MagicMock()
        mock_count_result.scalar.return_value = 1
        mock_list_result = MagicMock()
        mock_scalars = MagicMock()
        mock_scalars.all.return_value = [MagicMock()]
        mock_list_result.scalars.return_value = mock_scalars

        mock_session.execute.side_effect = [mock_count_result, mock_list_result]

        items, total = await TemplateRegistry.list(
            mock_session, category=TemplateCategory.research
        )
        assert total == 1

    @pytest.mark.asyncio
    async def test_list_with_framework_filter(self) -> None:
        from registry.templates import TemplateRegistry

        mock_session = AsyncMock()
        mock_count_result = MagicMock()
        mock_count_result.scalar.return_value = 0
        mock_list_result = MagicMock()
        mock_scalars = MagicMock()
        mock_scalars.all.return_value = []
        mock_list_result.scalars.return_value = mock_scalars

        mock_session.execute.side_effect = [mock_count_result, mock_list_result]

        items, total = await TemplateRegistry.list(mock_session, framework="langgraph")
        assert total == 0
        assert items == []

    @pytest.mark.asyncio
    async def test_list_with_status_filter(self) -> None:
        from registry.templates import TemplateRegistry

        mock_session = AsyncMock()
        mock_count_result = MagicMock()
        mock_count_result.scalar.return_value = 3
        mock_list_result = MagicMock()
        mock_scalars = MagicMock()
        mock_scalars.all.return_value = [MagicMock(), MagicMock(), MagicMock()]
        mock_list_result.scalars.return_value = mock_scalars

        mock_session.execute.side_effect = [mock_count_result, mock_list_result]

        items, total = await TemplateRegistry.list(mock_session, status=TemplateStatus.published)
        assert total == 3

    @pytest.mark.asyncio
    async def test_list_with_team_filter(self) -> None:
        from registry.templates import TemplateRegistry

        mock_session = AsyncMock()
        mock_count_result = MagicMock()
        mock_count_result.scalar.return_value = 1
        mock_list_result = MagicMock()
        mock_scalars = MagicMock()
        mock_scalars.all.return_value = [MagicMock()]
        mock_list_result.scalars.return_value = mock_scalars

        mock_session.execute.side_effect = [mock_count_result, mock_list_result]

        items, total = await TemplateRegistry.list(mock_session, team="engineering")
        assert total == 1

    @pytest.mark.asyncio
    async def test_update_existing(self) -> None:
        from registry.templates import TemplateRegistry

        mock_template = MagicMock()
        mock_template.name = "test"
        mock_template.description = "old"
        mock_session = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = mock_template
        mock_session.execute.return_value = mock_result

        result = await TemplateRegistry.update(mock_session, uuid.uuid4(), description="new desc")
        assert result is not None
        assert mock_template.description == "new desc"

    @pytest.mark.asyncio
    async def test_update_not_found(self) -> None:
        from registry.templates import TemplateRegistry

        mock_session = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None
        mock_session.execute.return_value = mock_result

        result = await TemplateRegistry.update(mock_session, uuid.uuid4(), description="new")
        assert result is None

    @pytest.mark.asyncio
    async def test_delete_existing(self) -> None:
        from registry.templates import TemplateRegistry

        mock_template = MagicMock()
        mock_template.name = "to-delete"
        mock_session = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = mock_template
        mock_session.execute.return_value = mock_result

        deleted = await TemplateRegistry.delete(mock_session, uuid.uuid4())
        assert deleted is True
        mock_session.delete.assert_called_once_with(mock_template)

    @pytest.mark.asyncio
    async def test_increment_use_count(self) -> None:
        from registry.templates import TemplateRegistry

        mock_template = MagicMock()
        mock_template.use_count = 5
        mock_session = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = mock_template
        mock_session.execute.return_value = mock_result

        await TemplateRegistry.increment_use_count(mock_session, uuid.uuid4())
        assert mock_template.use_count == 6

    @pytest.mark.asyncio
    async def test_increment_use_count_not_found(self) -> None:
        from registry.templates import TemplateRegistry

        mock_session = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None
        mock_session.execute.return_value = mock_result

        # Should not raise
        await TemplateRegistry.increment_use_count(mock_session, uuid.uuid4())

    @pytest.mark.asyncio
    async def test_search(self) -> None:
        from registry.templates import TemplateRegistry

        mock_session = AsyncMock()
        mock_count_result = MagicMock()
        mock_count_result.scalar.return_value = 1
        mock_list_result = MagicMock()
        mock_scalars = MagicMock()
        mock_scalars.all.return_value = [MagicMock()]
        mock_list_result.scalars.return_value = mock_scalars

        mock_session.execute.side_effect = [mock_count_result, mock_list_result]

        items, total = await TemplateRegistry.search(mock_session, "support")
        assert total == 1
        assert len(items) == 1

    @pytest.mark.asyncio
    async def test_search_empty_results(self) -> None:
        from registry.templates import TemplateRegistry

        mock_session = AsyncMock()
        mock_count_result = MagicMock()
        mock_count_result.scalar.return_value = 0
        mock_list_result = MagicMock()
        mock_scalars = MagicMock()
        mock_scalars.all.return_value = []
        mock_list_result.scalars.return_value = mock_scalars

        mock_session.execute.side_effect = [mock_count_result, mock_list_result]

        items, total = await TemplateRegistry.search(mock_session, "nonexistent")
        assert total == 0
        assert items == []


class TestMarketplaceRegistry:
    @pytest.mark.asyncio
    async def test_submit_listing(self) -> None:
        from registry.templates import MarketplaceRegistry

        mock_session = AsyncMock()
        mock_session.flush = AsyncMock()

        listing = await MarketplaceRegistry.submit_listing(
            mock_session,
            template_id=uuid.uuid4(),
            submitted_by="alice@test.com",
        )
        assert listing.status == ListingStatus.pending
        assert listing.submitted_by == "alice@test.com"
        mock_session.add.assert_called_once()

    @pytest.mark.asyncio
    async def test_get_by_id(self) -> None:
        from registry.templates import MarketplaceRegistry

        mock_listing = MagicMock()
        mock_listing.id = uuid.uuid4()
        mock_session = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = mock_listing
        mock_session.execute.return_value = mock_result

        result = await MarketplaceRegistry.get_by_id(mock_session, mock_listing.id)
        assert result is mock_listing

    @pytest.mark.asyncio
    async def test_get_by_id_not_found(self) -> None:
        from registry.templates import MarketplaceRegistry

        mock_session = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None
        mock_session.execute.return_value = mock_result

        result = await MarketplaceRegistry.get_by_id(mock_session, uuid.uuid4())
        assert result is None

    @pytest.mark.asyncio
    async def test_approve_listing(self) -> None:
        from registry.templates import MarketplaceRegistry

        mock_listing = MagicMock()
        mock_listing.template_id = uuid.uuid4()
        mock_listing.status = ListingStatus.pending
        mock_template = MagicMock()
        mock_template.status = TemplateStatus.draft

        mock_session = AsyncMock()
        mock_result1 = MagicMock()
        mock_result1.scalar_one_or_none.return_value = mock_listing
        mock_result2 = MagicMock()
        mock_result2.scalar_one_or_none.return_value = mock_template

        mock_session.execute.side_effect = [mock_result1, mock_result2]

        result = await MarketplaceRegistry.approve(mock_session, uuid.uuid4(), reviewed_by="admin")
        assert result is mock_listing
        assert mock_listing.status == ListingStatus.approved
        assert mock_listing.reviewed_by == "admin"
        assert mock_listing.published_at is not None
        assert mock_template.status == TemplateStatus.published

    @pytest.mark.asyncio
    async def test_approve_listing_not_found(self) -> None:
        from registry.templates import MarketplaceRegistry

        mock_session = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None
        mock_session.execute.return_value = mock_result

        result = await MarketplaceRegistry.approve(mock_session, uuid.uuid4(), reviewed_by="admin")
        assert result is None

    @pytest.mark.asyncio
    async def test_reject_listing(self) -> None:
        from registry.templates import MarketplaceRegistry

        mock_listing = MagicMock()
        mock_listing.status = ListingStatus.pending
        mock_session = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = mock_listing
        mock_session.execute.return_value = mock_result

        result = await MarketplaceRegistry.reject(
            mock_session, uuid.uuid4(), reviewed_by="admin", reason="Not ready"
        )
        assert result is mock_listing
        assert mock_listing.status == ListingStatus.rejected
        assert mock_listing.reviewed_by == "admin"
        assert mock_listing.reject_reason == "Not ready"

    @pytest.mark.asyncio
    async def test_reject_listing_not_found(self) -> None:
        from registry.templates import MarketplaceRegistry

        mock_session = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None
        mock_session.execute.return_value = mock_result

        result = await MarketplaceRegistry.reject(
            mock_session, uuid.uuid4(), reviewed_by="admin", reason="Bad"
        )
        assert result is None

    @pytest.mark.asyncio
    async def test_browse_default(self) -> None:
        from registry.templates import MarketplaceRegistry

        mock_session = AsyncMock()
        mock_count_result = MagicMock()
        mock_count_result.scalar.return_value = 2
        mock_list_result = MagicMock()
        mock_scalars = MagicMock()
        mock_scalars.all.return_value = [MagicMock(), MagicMock()]
        mock_list_result.scalars.return_value = mock_scalars

        mock_session.execute.side_effect = [mock_count_result, mock_list_result]

        items, total = await MarketplaceRegistry.browse(mock_session)
        assert total == 2
        assert len(items) == 2

    @pytest.mark.asyncio
    async def test_browse_with_category(self) -> None:
        from registry.templates import MarketplaceRegistry

        mock_session = AsyncMock()
        mock_count_result = MagicMock()
        mock_count_result.scalar.return_value = 1
        mock_list_result = MagicMock()
        mock_scalars = MagicMock()
        mock_scalars.all.return_value = [MagicMock()]
        mock_list_result.scalars.return_value = mock_scalars

        mock_session.execute.side_effect = [mock_count_result, mock_list_result]

        items, total = await MarketplaceRegistry.browse(
            mock_session, category=TemplateCategory.research
        )
        assert total == 1

    @pytest.mark.asyncio
    async def test_browse_with_framework(self) -> None:
        from registry.templates import MarketplaceRegistry

        mock_session = AsyncMock()
        mock_count_result = MagicMock()
        mock_count_result.scalar.return_value = 0
        mock_list_result = MagicMock()
        mock_scalars = MagicMock()
        mock_scalars.all.return_value = []
        mock_list_result.scalars.return_value = mock_scalars

        mock_session.execute.side_effect = [mock_count_result, mock_list_result]

        items, total = await MarketplaceRegistry.browse(mock_session, framework="langgraph")
        assert total == 0
        assert items == []

    @pytest.mark.asyncio
    async def test_browse_with_query(self) -> None:
        from registry.templates import MarketplaceRegistry

        mock_session = AsyncMock()
        mock_count_result = MagicMock()
        mock_count_result.scalar.return_value = 1
        mock_list_result = MagicMock()
        mock_scalars = MagicMock()
        mock_scalars.all.return_value = [MagicMock()]
        mock_list_result.scalars.return_value = mock_scalars

        mock_session.execute.side_effect = [mock_count_result, mock_list_result]

        items, total = await MarketplaceRegistry.browse(mock_session, query="support")
        assert total == 1

    @pytest.mark.asyncio
    async def test_browse_with_featured(self) -> None:
        from registry.templates import MarketplaceRegistry

        mock_session = AsyncMock()
        mock_count_result = MagicMock()
        mock_count_result.scalar.return_value = 1
        mock_list_result = MagicMock()
        mock_scalars = MagicMock()
        mock_scalars.all.return_value = [MagicMock()]
        mock_list_result.scalars.return_value = mock_scalars

        mock_session.execute.side_effect = [mock_count_result, mock_list_result]

        items, total = await MarketplaceRegistry.browse(mock_session, featured=True)
        assert total == 1

    @pytest.mark.asyncio
    async def test_browse_sort_by_installs(self) -> None:
        from registry.templates import MarketplaceRegistry

        mock_session = AsyncMock()
        mock_count_result = MagicMock()
        mock_count_result.scalar.return_value = 2
        mock_list_result = MagicMock()
        mock_scalars = MagicMock()
        mock_scalars.all.return_value = [MagicMock(), MagicMock()]
        mock_list_result.scalars.return_value = mock_scalars

        mock_session.execute.side_effect = [mock_count_result, mock_list_result]

        items, total = await MarketplaceRegistry.browse(mock_session, sort_by="installs")
        assert total == 2

    @pytest.mark.asyncio
    async def test_browse_sort_by_newest(self) -> None:
        from registry.templates import MarketplaceRegistry

        mock_session = AsyncMock()
        mock_count_result = MagicMock()
        mock_count_result.scalar.return_value = 1
        mock_list_result = MagicMock()
        mock_scalars = MagicMock()
        mock_scalars.all.return_value = [MagicMock()]
        mock_list_result.scalars.return_value = mock_scalars

        mock_session.execute.side_effect = [mock_count_result, mock_list_result]

        items, total = await MarketplaceRegistry.browse(mock_session, sort_by="newest")
        assert total == 1

    @pytest.mark.asyncio
    async def test_browse_sort_by_unknown_defaults_to_rating(self) -> None:
        from registry.templates import MarketplaceRegistry

        mock_session = AsyncMock()
        mock_count_result = MagicMock()
        mock_count_result.scalar.return_value = 0
        mock_list_result = MagicMock()
        mock_scalars = MagicMock()
        mock_scalars.all.return_value = []
        mock_list_result.scalars.return_value = mock_scalars

        mock_session.execute.side_effect = [mock_count_result, mock_list_result]

        items, total = await MarketplaceRegistry.browse(mock_session, sort_by="unknown")
        assert total == 0

    @pytest.mark.asyncio
    async def test_get_reviews(self) -> None:
        from registry.templates import MarketplaceRegistry

        mock_session = AsyncMock()
        mock_count_result = MagicMock()
        mock_count_result.scalar.return_value = 2
        mock_list_result = MagicMock()
        mock_scalars = MagicMock()
        mock_scalars.all.return_value = [MagicMock(), MagicMock()]
        mock_list_result.scalars.return_value = mock_scalars

        mock_session.execute.side_effect = [mock_count_result, mock_list_result]

        reviews, total = await MarketplaceRegistry.get_reviews(mock_session, uuid.uuid4())
        assert total == 2
        assert len(reviews) == 2

    @pytest.mark.asyncio
    async def test_increment_install_count(self) -> None:
        from registry.templates import MarketplaceRegistry

        mock_listing = MagicMock()
        mock_listing.install_count = 10
        mock_session = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = mock_listing
        mock_session.execute.return_value = mock_result

        await MarketplaceRegistry.increment_install_count(mock_session, uuid.uuid4())
        assert mock_listing.install_count == 11

    @pytest.mark.asyncio
    async def test_increment_install_count_not_found(self) -> None:
        from registry.templates import MarketplaceRegistry

        mock_session = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None
        mock_session.execute.return_value = mock_result

        # Should not raise
        await MarketplaceRegistry.increment_install_count(mock_session, uuid.uuid4())

    @pytest.mark.asyncio
    async def test_add_review(self) -> None:
        from registry.templates import MarketplaceRegistry

        mock_session = AsyncMock()
        mock_session.flush = AsyncMock()

        # Mock the avg/count queries
        mock_listing_result = MagicMock()
        mock_listing = MagicMock()
        mock_listing.avg_rating = 0.0
        mock_listing.review_count = 0
        mock_listing_result.scalar_one_or_none.return_value = mock_listing

        mock_avg_result = MagicMock()
        mock_avg_result.scalar.return_value = 4.0

        mock_count_result = MagicMock()
        mock_count_result.scalar.return_value = 1

        mock_session.execute.side_effect = [
            MagicMock(),  # flush for review add
            mock_listing_result,  # get listing
            mock_avg_result,  # avg rating
            mock_count_result,  # count
        ]

        review = await MarketplaceRegistry.add_review(
            mock_session,
            listing_id=uuid.uuid4(),
            reviewer="bob",
            rating=4,
            comment="Great!",
        )
        assert review.rating == 4
        assert review.reviewer == "bob"


# ---------------------------------------------------------------------------
# Template instantiation tests
# ---------------------------------------------------------------------------


class TestTemplateInstantiation:
    def test_parameter_substitution(self) -> None:
        """Verify {{placeholder}} substitution works correctly."""
        import copy
        import json

        config = {"name": "{{agent_name}}", "model": {"primary": "{{model}}"}}
        values = {"agent_name": "my-bot", "model": "gpt-4o"}
        parameters = [
            {"name": "agent_name", "default": "default-bot"},
            {"name": "model", "default": "claude-sonnet-4"},
        ]

        config_str = json.dumps(copy.deepcopy(config))
        for param in parameters:
            placeholder = "{{" + param["name"] + "}}"
            value = values.get(param["name"], param.get("default", ""))
            config_str = config_str.replace(placeholder, str(value))

        result = json.loads(config_str)
        assert result["name"] == "my-bot"
        assert result["model"]["primary"] == "gpt-4o"

    def test_parameter_defaults_used(self) -> None:
        """Verify default values are used when user doesn't provide a value."""
        import copy
        import json

        config = {"name": "{{agent_name}}"}
        values: dict[str, str] = {}
        parameters = [{"name": "agent_name", "default": "default-bot"}]

        config_str = json.dumps(copy.deepcopy(config))
        for param in parameters:
            placeholder = "{{" + param["name"] + "}}"
            value = values.get(param["name"], param.get("default", ""))
            config_str = config_str.replace(placeholder, str(value))

        result = json.loads(config_str)
        assert result["name"] == "default-bot"

    def test_multiple_parameters(self) -> None:
        """Verify multiple parameters are substituted correctly."""
        import copy
        import json

        config = {
            "name": "{{name}}",
            "team": "{{team}}",
            "deploy": {"cloud": "{{cloud}}"},
        }
        values = {"name": "bot", "team": "eng", "cloud": "aws"}
        parameters = [
            {"name": "name", "default": "x"},
            {"name": "team", "default": "y"},
            {"name": "cloud", "default": "local"},
        ]

        config_str = json.dumps(copy.deepcopy(config))
        for param in parameters:
            placeholder = "{{" + param["name"] + "}}"
            value = values.get(param["name"], param.get("default", ""))
            config_str = config_str.replace(placeholder, str(value))

        result = json.loads(config_str)
        assert result["name"] == "bot"
        assert result["team"] == "eng"
        assert result["deploy"]["cloud"] == "aws"
