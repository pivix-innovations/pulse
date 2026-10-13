"""Template version storage: numbering, lookup, published pointer, cascade delete, find."""
from __future__ import annotations

from domain.entities.template import Template, TemplateVersion
from domain.enums import ChannelType, TemplateFormat
from domain.value_objects.ids import TemplateId, TemplateVersionId, TenantId


async def add_template(env, tenant, name="welcome", locale="en", channel=ChannelType.EMAIL):
    t = Template(id=TemplateId.new(), tenant_id=tenant, name=name, channel=channel, body="draft",
                 subject="s", locale=locale, format=TemplateFormat.TEXT)
    async with env.uow() as uow:
        await uow.templates.add(t)
        await uow.commit()
    return t


def version(t, body):
    return TemplateVersion(id=TemplateVersionId.new(), tenant_id=t.tenant_id, template_id=t.id, version=0,
                           body=body, subject="s", format=TemplateFormat.TEXT, note="n")


async def test_versions_are_numbered_per_template_from_one(env):
    tenant = TenantId.new()
    a, b = await add_template(env, tenant, "a"), await add_template(env, tenant, "b")
    async with env.uow() as uow:
        got = [(await uow.templates.add_version(version(t, body))).version for t, body in ((a, "1"), (a, "2"), (b, "1"), (a, "3"))]
        await uow.commit()
    assert got == [1, 2, 1, 3]


async def test_versions_list_newest_first_and_are_fetchable_by_number(env):
    tenant = TenantId.new()
    t = await add_template(env, tenant)
    async with env.uow() as uow:
        for body in ("one", "two", "three"):
            await uow.templates.add_version(version(t, body))
        await uow.commit()
        assert [v.body for v in await uow.templates.list_versions(tenant, t.id)] == ["three", "two", "one"]
        assert (await uow.templates.get_version(tenant, t.id, 2)).body == "two"
        assert await uow.templates.get_version(tenant, t.id, 9) is None
        assert await uow.templates.get_version(TenantId.new(), t.id, 2) is None      # another tenant


async def test_the_published_pointer_and_map(env):
    tenant = TenantId.new()
    t, other = await add_template(env, tenant, "a"), await add_template(env, tenant, "b")
    async with env.uow() as uow:
        await uow.templates.add_version(version(t, "one"))
        await uow.templates.add_version(version(t, "two"))
        await uow.templates.set_published(tenant, t.id, 1)
        await uow.commit()
    async with env.uow() as uow:
        assert (await uow.templates.get(tenant, t.id)).published_version == 1
        published = await uow.templates.list_published(tenant)
        assert {k: v.body for k, v in published.items()} == {t.id: "one"}      # unpublished templates are absent
        assert (await uow.templates.get(tenant, other.id)).published_version is None


async def test_updating_a_template_does_not_move_the_published_pointer(env):
    tenant = TenantId.new()
    t = await add_template(env, tenant)
    async with env.uow() as uow:
        await uow.templates.add_version(version(t, "one"))
        await uow.templates.set_published(tenant, t.id, 1)
        t.body = "edited draft"
        t.published_version = 1   # the API carries it over; the repo must not need to write it
        await uow.templates.update(t)
        await uow.commit()
    async with env.uow() as uow:
        got = await uow.templates.get(tenant, t.id)
    assert got.body == "edited draft" and got.published_version == 1


async def test_find_returns_every_locale_of_a_name_and_channel_within_the_tenant(env):
    tenant, stranger = TenantId.new(), TenantId.new()
    await add_template(env, tenant, "welcome", "en")
    await add_template(env, tenant, "welcome", "fr")
    await add_template(env, tenant, "welcome", "en", ChannelType.SMS)
    await add_template(env, tenant, "other", "en")
    await add_template(env, stranger, "welcome", "en")
    async with env.uow() as uow:
        found = await uow.templates.find(tenant, "welcome", ChannelType.EMAIL)
    assert sorted(t.locale for t in found) == ["en", "fr"]


async def test_deleting_a_template_deletes_its_versions(env):
    tenant = TenantId.new()
    t = await add_template(env, tenant)
    async with env.uow() as uow:
        await uow.templates.add_version(version(t, "one"))
        await uow.commit()
    async with env.uow() as uow:
        assert await uow.templates.delete(tenant, t.id)
        await uow.commit()
    async with env.uow() as uow:
        assert await uow.templates.list_versions(tenant, t.id) == []
